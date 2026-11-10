"""Scenario checks of the gateway core on simulated time, runnable against the real module or a mutant (``GATEWAY_DESIGN`` section 8)."""

from __future__ import annotations

import random
import types
from collections.abc import Callable
from pathlib import Path

from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.connector import ErrorClass as E
from hoshitori.governor import Priority as P
from hoshitori.governor import RateModel
from tests.gateway_support import FakeAdapter, Harness, at, ok, response, seconds_of


class FixedRandom(random.Random):
    """A generator whose uniform draw is the top of the range, so backoff waits are exact."""

    def uniform(self, a: float, b: float) -> float:
        return b


def new_dir(tmp: Path, name: str) -> Path:
    path = tmp / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def basic(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    heard: list[object] = []
    h = Harness(mod, new_dir(tmp, "basic"), lambda d: ok(b'{"status":"ok","n":1}'), on_event=heard.append)
    cid = h.submit(h.call(symbol="secretword"))
    if h.gateway.pending() != 1 or h.gateway.queue_depth() != 1 or h.gateway.queue_depth(P.P0) != 1 or h.gateway.queue_depth(P.P2) != 0:
        bad.append(f"a queued call: pending {h.gateway.pending()}, depth {h.gateway.queue_depth()}")
    twin = h.submit(h.call(symbol="secretword", caller="another"))
    if h.gateway.pending() != 2 or h.gateway.queue_depth() != 1:
        bad.append(f"a call that shares a fetch: pending {h.gateway.pending()}, depth {h.gateway.queue_depth()}")
    h.tick_at(0)
    if h.gateway.queue_depth() != 0 or h.gateway.pending() != 2:
        bad.append(f"a call in flight counts as queued: depth {h.gateway.queue_depth()}, pending {h.gateway.pending()}")
    h.run(10)
    if heard != h.gateway.events or not heard:
        bad.append("the event hook did not hear every event")
    if h.result(twin) is None or h.result(twin).shared_with != cid:
        bad.append("the twin did not share the fetch")
    h.submit(h.call(op="deep", symbol="other"))
    h.run(20)
    if h.governor.available(at(20)) != 6 - 1 - 3:
        bad.append(f"a call must pay the cost its adapter plans: {h.governor.available(at(20))} left")
    cid = cid
    r = h.result(cid)
    if (
        r is None
        or r.outcome is not mod.Outcome.OK
        or r.attempts != 1
        or r.queue_wait_s != 0
        or r.error is not None
        or r.from_cache
        or r.shared_with is not None
    ):
        bad.append(f"a plain call did not complete cleanly: {r}")
        return bad
    capture = h.store.get(r.capture_id)
    if capture.body != b'{"status":"ok","n":1}' or capture.status != 200 or capture.source != "ryo":
        bad.append("the response was not stored as received")
    if r.shared_with is not None:
        bad.append("the first call shares nobody's fetch")
    kinds = [e.kind for e in h.gateway.events if e.call_id == cid]
    if kinds != ["SUBMITTED", "DISPATCHED", "FINISHED", "COMPLETED"]:
        bad.append(f"events {kinds}")
    if any("SECRETWORD" in repr(e) or "secretword" in repr(e) for e in h.gateway.events):
        bad.append("an event carries the arguments of a call")
    if "SECRETWORD" in h.journal.path.read_text():
        bad.append("the journal carries the arguments of a call")
    if h.gateway.pending() != 0 or h.gateway.queue_depth() != 0:
        bad.append("a finished call is still pending")
    done = next(e for e in h.gateway.events if e.kind == "COMPLETED")
    if (
        done.detail != "OK"
        or done.capture_id != r.capture_id
        or done.priority != "P0"
        or done.provider != "ryo"
        or done.operation != "analyze"
    ):
        bad.append(f"the completion event is wrong: {done}")
    if done.breaker != "CLOSED" or done.budget_left != 5:
        bad.append("the completion event lacks the budget or breaker state")
    return bad


def refusals(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "refusals"))

    def refused(label: str, call, reason: str, error_class=None) -> None:  # type: ignore[no-untyped-def]
        cid = h.gateway.submit(call, at(0))
        r = h.result(cid)
        if r is None or r.outcome is not mod.Outcome.REFUSED or r.reason != reason or r.attempts != 0:
            bad.append(f"{label}: {r}")
        elif (r.error.error_class if r.error else None) != error_class:
            bad.append(f"{label}: error {r.error}")

    refused("unknown provider", mod.Call("nobody", "analyze", {"symbol": "A"}, P.P0, at(100), "c"), "UNKNOWN_PROVIDER")
    refused("unknown operation", h.call(op="trade"), "UNKNOWN_OPERATION")
    refused("missing argument", mod.Call("ryo", "analyze", {}, P.P0, at(100), "c"), "INVALID_ARGS", E.INVALID_ARGS)
    refused("extra argument", mod.Call("ryo", "analyze", {"symbol": "A", "x": 1}, P.P0, at(100), "c"), "INVALID_ARGS", E.INVALID_ARGS)
    refused("unknown namespace", h.call(namespace="STAGING"), "UNKNOWN_NAMESPACE")
    refused("a league class outside the league", h.call(priority=P.P0, namespace=mod.ON_DEMAND), "LEAGUE_CLASS_OUTSIDE_THE_LEAGUE")
    refused("an on-demand class in the league", h.call(priority=P.P2), "ON_DEMAND_CLASS_IN_THE_LEAGUE")
    refused("another on-demand class in the league", h.call(priority=P.P3), "ON_DEMAND_CLASS_IN_THE_LEAGUE")
    for ok_class in (P.PA, P.PJ, P.P1):
        cid = h.gateway.submit(h.call(priority=ok_class, symbol=ok_class.value), at(0))
        if h.result(cid) is not None:
            bad.append(f"{ok_class.value} in the league was refused or finished at once")
    on_demand = h.gateway.submit(h.call(priority=P.P2, namespace=mod.ON_DEMAND, symbol="od2"), at(0))
    if h.result(on_demand) is not None:
        bad.append("an on-demand class on demand was refused")
    h.run(5)
    if h.dispatched and h.dispatched[0][1].provider != "ryo":
        bad.append("a refused call was dispatched")
    if any(d.args.get("symbol") in ("A",) for _, d in h.dispatched):
        bad.append("a refused call reached the provider")
    # a refused call neither spends nor queues
    h2 = Harness(mod, new_dir(tmp, "refusals2"))
    h2.gateway.submit(h2.call(op="trade"), at(0))
    if h2.gateway.pending() != 0 or h2.governor.available(at(0)) != 6:
        bad.append("a refused call used the queue or the budget")
    return bad


def deadlines(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "deadlines"))
    cid = h.submit(h.call(deadline=4.9))
    r = h.result(cid)
    if r is None or r.outcome is not mod.Outcome.DEADLINE_UNREACHABLE or r.attempts != 0 or h.gateway.pending() != 0:
        bad.append(f"a call with less than a minimal attempt left was queued: {r}")
    cid = h.submit(h.call(deadline=5.0, symbol="B"))
    if h.result(cid) is not None:
        bad.append("a call with exactly a minimal attempt left was refused")
    h.run(3)
    r = h.result(cid)
    if r is None or r.outcome is not mod.Outcome.OK:
        bad.append(f"a call with exactly a minimal attempt left did not run: {r}")
    # a call that cannot get a token in time is completed when that is known, not left to rot
    h = Harness(mod, new_dir(tmp, "deadlines2"), lambda d: ok(latency=1.0), limit=1, concurrency=1)
    first = h.submit(h.call(symbol="A", deadline=20))
    second = h.submit(h.call(symbol="B", deadline=62))
    h.run(40)
    r1, r2 = h.result(first), h.result(second)
    if r1 is None or r1.outcome is not mod.Outcome.OK:
        bad.append("the first call did not run")
    if r2 is None or r2.outcome is not mod.Outcome.DEADLINE_UNREACHABLE:
        bad.append(f"a call that cannot get a token in time was not completed: {r2}")
    elif r2.queue_position is None or r2.reason != "TOKENS":
        bad.append(f"the unreachable call does not say why or where it was queued: {r2}")
    if any(d.args.get("symbol") == "B" for _, d in h.dispatched):
        bad.append("an unreachable call was sent")
    # a call that waits for a free slot until it can no longer finish is completed at that point
    h = Harness(mod, new_dir(tmp, "deadlines3"), lambda d: ok(latency=100.0), limit=100, concurrency=1)
    h.submit(h.call(symbol="A", deadline=28))  # the earlier deadline goes first and holds the only slot
    waiting = h.submit(h.call(symbol="B", deadline=30))
    h.run(24)
    if h.result(waiting) is not None:
        bad.append("a call that can still make its deadline was completed early")
    h.run(25.5)
    r = h.result(waiting)
    if r is None or r.outcome is not mod.Outcome.DEADLINE_UNREACHABLE or r.reason != "DEADLINE" or r.attempts != 0:
        bad.append(f"a call whose time ran out in the queue is still waiting: {r}")
    elif not 25 < r.queue_wait_s < 25.01:
        bad.append(f"a call that never left the queue waited {r.queue_wait_s} s, until it could no longer finish")
    # a call in flight is not completed because the time left shrinks below a minimal attempt
    h = Harness(mod, new_dir(tmp, "deadlines5"), lambda d: ok(latency=28.0), limit=100)
    flying = h.submit(h.call(symbol="A", deadline=30))
    h.run(26)
    h.submit(h.call(symbol="B", deadline=1000))
    h.run(27)
    if h.result(flying) is not None:
        bad.append("a call in flight was completed before its response arrived")
    h.run(29)
    if h.result(flying) is None or h.result(flying).outcome is not mod.Outcome.OK:
        bad.append(f"a call in flight did not complete: {h.result(flying)}")
    # a request that costs more than the provider's whole allowance can never be sent
    big = FakeAdapter(mod, operations={"huge": 10.0, "analyze": 1.0})
    h = Harness(mod, new_dir(tmp, "deadlines6"), limit=6, adapter=big)
    cid = h.submit(h.call(op="huge", deadline=5000))
    h.run(2)
    r = h.result(cid)
    if r is None or r.outcome is not mod.Outcome.DEADLINE_UNREACHABLE or r.reason != "TOO_EXPENSIVE":
        bad.append(f"a request above the whole allowance was kept waiting: {r}")
    # a token that arrives in time is used
    h = Harness(mod, new_dir(tmp, "deadlines4"), lambda d: ok(latency=1.0), limit=1, concurrency=1)
    h.submit(h.call(symbol="A", deadline=20))
    late = h.submit(h.call(symbol="B", deadline=66))
    h.run(70)
    if h.result(late) is None or h.result(late).outcome is not mod.Outcome.OK or h.sent_times() != [0, 60]:
        bad.append(f"a call that can just make its deadline: {h.result(late)} {h.sent_times()}")
    elif h.result(late).queue_wait_s != 60:
        bad.append(f"queue wait {h.result(late).queue_wait_s}")
    return bad


def rate(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "rate"), lambda d: ok(latency=2.0))
    ids = [h.submit(h.call(symbol=f"S{i}")) for i in range(8)]
    h.run(100)
    times = sorted(h.sent_times())
    if times != [0, 0, 0, 2, 2, 2, 60, 60]:
        bad.append(f"dispatch times {times}, expected six in the first window (three at a time) and two in the next")
    if any(h.result(i) is None or h.result(i).outcome is not mod.Outcome.OK for i in ids):
        bad.append("not every call completed OK")
    # token bucket
    h = Harness(mod, new_dir(tmp, "bucket"), lambda d: ok(latency=0.5), model=RateModel.TOKEN_BUCKET, concurrency=10)
    for i in range(8):
        h.submit(h.call(symbol=f"S{i}"))
    h.run(100)
    times = sorted(round(t, 3) for t in h.sent_times())
    if times != [0, 0, 0, 0, 0, 0, 10, 20]:
        bad.append(f"token bucket dispatch times {times}")
    # a header that says less is left than we believed
    h = Harness(mod, new_dir(tmp, "headers"), lambda d: ok(headers={"X-RateLimit-Remaining": "0"}), concurrency=1)
    first = h.submit(h.call(symbol="A"))
    h.submit(h.call(symbol="B", deadline=1000))
    h.run(10)
    if h.result(first) is None or [t for t in h.sent_times() if t > 0]:
        bad.append("the provider said nothing was left and a second call was still sent")
    h.run(61)
    if len(h.dispatched) != 2:
        bad.append("the second call was not sent after the window rolled")
    # a header that says more than we believed is believed only when the provider's own window says so; a stale header is not
    return bad


def ordering(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "order"), lambda d: ok(latency=10.0), limit=100, concurrency=1)
    h.submit(h.call(symbol="BUSY"))
    h.run(0)
    for pr, name, ns in [
        (P.P3, "p3", mod.ON_DEMAND),
        (P.P2, "p2", mod.ON_DEMAND),
        (P.P1, "p1", mod.LEAGUE),
        (P.P0, "p0", mod.LEAGUE),
        (P.PA, "pa", mod.LEAGUE),
        (P.PJ, "pj", mod.LEAGUE),
    ]:
        h.submit(h.call(priority=pr, symbol=name, namespace=ns, deadline=500))
    h.run(100)
    order = [d.args["symbol"] for _, d in h.dispatched]
    if order != ["BUSY", "PJ", "PA", "P0", "P1", "P2", "P3"]:
        bad.append(f"class order {order}")
    # earliest deadline first inside a class, then arrival
    h = Harness(mod, new_dir(tmp, "edf"), lambda d: ok(latency=10.0), limit=100, concurrency=1)
    h.submit(h.call(symbol="BUSY"))
    h.run(0)
    for name, dl in [("late", 300), ("early", 100), ("middle", 200), ("tie1", 400), ("tie2", 400)]:
        h.submit(h.call(symbol=name, deadline=dl))
    h.run(100)
    order = [d.args["symbol"] for _, d in h.dispatched]
    if order != ["BUSY", "EARLY", "MIDDLE", "LATE", "TIE1", "TIE2"]:
        bad.append(f"deadline order {order}")
    # the judge lane: below its floor it goes first, above it it queues with the league
    h = Harness(mod, new_dir(tmp, "judge"), lambda d: ok(latency=10.0), limit=100, concurrency=1, judge_floor=1)
    h.submit(h.call(symbol="BUSY"))
    h.run(0)
    h.submit(h.call(priority=P.P0, symbol="league1", deadline=100))
    h.submit(h.call(priority=P.PJ, symbol="judge1", deadline=900))
    h.submit(h.call(priority=P.PJ, symbol="judge2", deadline=900))
    h.submit(h.call(priority=P.P0, symbol="league2", deadline=200))
    h.run(100)
    order = [d.args["symbol"] for _, d in h.dispatched]
    if order != ["BUSY", "JUDGE1", "LEAGUE1", "LEAGUE2", "JUDGE2"]:
        bad.append(f"judge floor order {order}")
    h.submit(h.call(priority=P.PJ, symbol="judge3", deadline=900))
    h.submit(h.call(priority=P.P0, symbol="league3", deadline=900))
    h.run(130)  # a minute after judge1 was served the floor is open again
    order2 = [d.args["symbol"] for _, d in h.dispatched][5:]
    if order2 != ["JUDGE3", "LEAGUE3"] and order2 != ["LEAGUE3", "JUDGE3"]:
        bad.append(f"later calls {order2}")
    # the floor reopens exactly one window after the judge was served, not before
    for gap, expect_judge_first in ((59.9, False), (60.0, True)):
        h = Harness(mod, new_dir(tmp, f"judge-edge-{gap}"), lambda d: ok(latency=0.1), limit=100, concurrency=1, judge_floor=1)
        h.submit(h.call(priority=P.PJ, symbol="first", deadline=900))
        h.run(gap)
        h.submit(h.call(priority=P.P0, symbol="league", deadline=100))
        h.submit(h.call(priority=P.PJ, symbol="judge", deadline=900))
        h.run(gap + 1)
        order = [d.args["symbol"] for _, d in h.dispatched][1:]
        if order != (["JUDGE", "LEAGUE"] if expect_judge_first else ["LEAGUE", "JUDGE"]):
            bad.append(f"{gap} s after the judge was served the floor says {order}")
    # only the judge's own dispatches count toward its floor
    h = Harness(mod, new_dir(tmp, "judge-own"), lambda d: ok(latency=0.1), limit=100, concurrency=1, judge_floor=1)
    h.submit(h.call(priority=P.P0, symbol="league0", deadline=900))
    h.run(1)
    h.submit(h.call(priority=P.P0, symbol="league1", deadline=100))
    h.submit(h.call(priority=P.PJ, symbol="judge", deadline=900))
    h.run(2)
    order = [d.args["symbol"] for _, d in h.dispatched][1:]
    if order != ["JUDGE", "LEAGUE1"]:
        bad.append(f"other classes must not use up the judge's floor: {order}")
    h = Harness(mod, new_dir(tmp, "judge2"), lambda d: ok(latency=10.0), limit=100, concurrency=1, judge_floor=2)
    h.submit(h.call(symbol="BUSY"))
    h.run(0)
    h.submit(h.call(priority=P.P0, symbol="league1", deadline=100))
    for name in ("j1", "j2", "j3"):
        h.submit(h.call(priority=P.PJ, symbol=name, deadline=900))
    h.run(100)
    order = [d.args["symbol"] for _, d in h.dispatched]
    if order != ["BUSY", "J1", "J2", "LEAGUE1", "J3"]:
        bad.append(f"a floor of two: {order}")
    return bad


def concurrency(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "conc"), lambda d: ok(latency=10.0), limit=100, concurrency=2)
    for i in range(5):
        h.submit(h.call(symbol=f"S{i}"))
    h.run(100)
    if sorted(h.sent_times()) != [0, 0, 10, 10, 20]:
        bad.append(f"two at a time: {sorted(h.sent_times())}")
    try:
        mod.Gateway(h.store, h.journal).register(h.adapter, h.governor, h.breakers, concurrency=0)
        bad.append("a concurrency of zero was accepted")
    except mod.GatewayError:
        pass
    g = mod.Gateway(h.store, h.journal)
    g.register(h.adapter, h.governor, h.breakers)
    try:
        g.register(h.adapter, h.governor, h.breakers)
        bad.append("a provider was registered twice")
    except mod.GatewayError:
        pass
    try:
        g.declare_window("nobody", at(0), 1, at(10))
        bad.append("a window was declared for an unknown provider")
    except mod.GatewayError:
        pass
    return bad


def retries(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    # an upstream error that asks for a wait is retried after at least that wait, and every attempt is paid for
    attempts = iter([response(503, {"Retry-After": "5"}, latency=1.0), ok(latency=1.0)])
    h = Harness(mod, new_dir(tmp, "retry503"), lambda d: next(attempts), rng=FixedRandom(0))
    cid = h.submit(h.call())
    h.run(30)
    r = h.result(cid)
    if r is None or r.outcome is not mod.Outcome.OK or r.attempts != 2:
        bad.append(f"a retried call: {r}")
    if h.sent_times() != [0, 6]:
        bad.append(f"the retry was sent at {h.sent_times()}, expected 1 s of latency plus the 5 s asked for")
    if [d.attempt for _, d in h.dispatched] != [1, 2] or r.queue_wait_s != 0:
        bad.append(
            f"attempt numbers {[d.attempt for _, d in h.dispatched]}, queue wait {r.queue_wait_s} (the wait ends at the first dispatch)"
        )
    if h.governor.available(at(30)) != 4:
        bad.append(f"each attempt must be paid for: {h.governor.available(at(30))} left")
    if len(h.store) != 2:
        bad.append("every attempt's response must be stored")
    kinds = [e.kind for e in h.gateway.events]
    if kinds.count("RETRY_PLANNED") != 1:
        bad.append(f"events {kinds}")
    if h.breakers.provider.state(at(30)).value != "CLOSED":
        bad.append("one failure and a success must leave the breaker closed")
    # timeouts: two retries, then the error; each attempt leaves a capture with no status
    h = Harness(
        mod, new_dir(tmp, "timeouts"), lambda d: ok(latency=500.0), rng=FixedRandom(0), breaker=BreakerConfig(consecutive_failures=10)
    )
    cid = h.submit(h.call(deadline=2000))
    h.run(1000)
    r = h.result(cid)
    if (
        r is None
        or r.outcome is not mod.Outcome.FAILED
        or r.reason != "EXHAUSTED"
        or r.attempts != 3
        or r.error is None
        or r.error.error_class is not E.TIMEOUT
    ):
        bad.append(f"repeated timeouts: {r}")
    captures = [h.store.get(i) for i in h.store.ids()]
    if len(captures) != 3 or any(c.status is not None or c.body != b"" for c in captures):
        bad.append("a timeout must leave a capture with no status and no body")
    if captures and "arguments" not in captures[0].request:
        bad.append("the capture of a failed attempt lacks the request")
    if len(h.journal.entries()) != 3 or len(h.dispatched) != 3:
        bad.append("each timed-out attempt must be dispatched and journalled")
    if sum(1 for e in h.journal.entries() if e["cost"] == "1.0") != 3:
        bad.append("each attempt must be charged its cost")
    # the deadline, not a count, ends retries
    h = Harness(
        mod,
        new_dir(tmp, "deadline-retries"),
        lambda d: ok(latency=500.0),
        rng=FixedRandom(0),
        breaker=BreakerConfig(consecutive_failures=10),
    )
    cid = h.submit(h.call(deadline=100))
    h.run(200)
    r = h.result(cid)
    if r is None or r.outcome is not mod.Outcome.FAILED or r.reason != "DEADLINE" or r.attempts != 2:
        bad.append(f"retries near the deadline: {r}")
    times = h.sent_times()
    if times != [0, 60.5]:
        bad.append(f"the second attempt started at {times}")
    elif h.dispatched[1][1].timeout_s != 39.5:
        bad.append(f"the second attempt may not run past the deadline: timeout {h.dispatched[1][1].timeout_s}")
    # a failed call keeps the last response, and an open breaker ends a retrying call with the error that opened it
    h = Harness(
        mod,
        new_dir(tmp, "lastcapture"),
        lambda d: response(503, {"Retry-After": "1"}, b"down"),
        rng=FixedRandom(0),
        limit=100,
        breaker=BreakerConfig(consecutive_failures=2, cooldown_s=300),
    )
    cid = h.submit(h.call(deadline=1000))
    h.run(50)
    r = h.result(cid)
    if (
        r is None
        or r.outcome is not mod.Outcome.BREAKER_OPEN
        or r.attempts != 2
        or r.error is None
        or r.error.error_class is not E.UPSTREAM
    ):
        bad.append(f"a call stopped by a breaker that its own failures opened: {r}")
    h = Harness(mod, new_dir(tmp, "lastcapture2"), lambda d: response(401, body=b"no"))
    cid = h.submit(h.call())
    h.run(5)
    r = h.result(cid)
    if r is None or r.capture_id is None or h.store.get(r.capture_id).status != 401 or h.store.get(r.capture_id).body != b"no":
        bad.append("a failed call does not point at its last response")
    # errors that are never retried end the call at once
    for status, cls in ((400, E.INVALID_ARGS), (401, E.AUTH), (404, E.NOT_FOUND)):
        h = Harness(mod, new_dir(tmp, f"never{status}"), lambda d, s=status: response(s))
        cid = h.submit(h.call())
        h.run(10)
        r = h.result(cid)
        if (
            r is None
            or r.outcome is not mod.Outcome.FAILED
            or r.reason != "NOT_RETRYABLE"
            or r.attempts != 1
            or r.error is None
            or r.error.error_class is not cls
        ):
            bad.append(f"HTTP {status}: {r}")
    # back-pressure: one retry after the stated wait, and the whole provider waits
    answers = iter([response(429, {"Retry-After": "30"}), response(429, {"Retry-After": "30"}), ok()])
    h = Harness(mod, new_dir(tmp, "ratelimit"), lambda d: next(answers), concurrency=1, limit=100)
    first = h.submit(h.call(symbol="A", deadline=1000))
    other = h.submit(h.call(symbol="B", deadline=1000))
    h.run(200)
    r1, r2 = h.result(first), h.result(other)
    if h.sent_times()[:2] != [0, 31] and h.sent_times()[:2] != [0, 31.0]:
        bad.append(f"after a 429 the provider must wait: {h.sent_times()}")
    if (
        r1 is None
        or r1.outcome is not mod.Outcome.FAILED
        or r1.reason != "EXHAUSTED"
        or r1.attempts != 2
        or r1.error.error_class is not E.RATE_LIMIT
    ):
        bad.append(f"two 429s: {r1}")
    if r2 is None or r2.outcome is not mod.Outcome.OK:
        bad.append(f"the call that waited behind the 429: {r2}")
    elif sorted(h.sent_times())[2] < 62:
        bad.append(f"the second 429 must start a second wait: {h.sent_times()}")
    # a 429 with no stated wait waits the default
    answers = iter([response(429), ok(), ok()])
    h = Harness(mod, new_dir(tmp, "ratelimit2"), lambda d: next(answers), limit=100)
    cid = h.submit(h.call(deadline=1000))
    h.run(2)
    other = h.submit(h.call(symbol="OTHER", deadline=1000))
    h.run(200)
    if h.sent_times() != [0, 61, 61] or h.result(cid).outcome is not mod.Outcome.OK or h.result(other).outcome is not mod.Outcome.OK:
        bad.append(f"a 429 without a wait must hold every call for the default minute: {h.sent_times()}")
    return bad


def breakers(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "breaker"), lambda d: response(401), limit=100, concurrency=2)
    ids = [h.submit(h.call(symbol=f"S{i}")) for i in range(3)]
    h.run(10)
    if any(h.result(i).reason != "NOT_RETRYABLE" for i in ids):
        bad.append("three authentication failures were retried")
    sent = len(h.dispatched)
    fast = h.submit(h.call(symbol="LATER"))
    r = h.result(fast)
    h.run(11)
    r = h.result(fast)
    if r is None or r.outcome is not mod.Outcome.BREAKER_OPEN or r.attempts != 0 or r.reason != "OPEN":
        bad.append(f"an open breaker did not refuse at once: {r}")
    if len(h.dispatched) != sent:
        bad.append("a call was sent through an open breaker")
    if h.governor.available(at(11)) != 100 - sent:
        bad.append("a refused call used up budget")
    # after the cool-down one call is the trial; a second at the same time is refused
    h.script = lambda d: ok(latency=5.0)
    t = 10 + 30
    h.run(t)
    trial = h.submit(h.call(symbol="TRIAL"))
    second = h.submit(h.call(symbol="SECOND"))
    h.run(t + 1)
    if h.result(second) is None or h.result(second).outcome is not mod.Outcome.BREAKER_OPEN or h.result(second).reason != "TRIAL_IN_FLIGHT":
        bad.append(f"a second call during the trial: {h.result(second)}")
    h.run(t + 10)
    if h.result(trial) is None or h.result(trial).outcome is not mod.Outcome.OK:
        bad.append(f"the trial call: {h.result(trial)}")
    after = h.submit(h.call(symbol="AFTER"))
    h.run(t + 20)
    if h.result(after) is None or h.result(after).outcome is not mod.Outcome.OK:
        bad.append("calls did not flow again after the trial succeeded")
    # a half-open trial that the budget refuses is not used up
    h = Harness(
        mod,
        new_dir(tmp, "trial-back"),
        lambda d: response(401),
        limit=1,
        concurrency=1,
        breaker=BreakerConfig(consecutive_failures=1, cooldown_s=10),
    )
    h.submit(h.call(symbol="A", deadline=20))
    h.run(12)  # the breaker opened at t=1 and is half-open from t=11
    h.script = lambda d: ok()
    waiting = h.submit(h.call(symbol="B", deadline=300))
    h.run(30)  # the only token is back at t=60
    if h.result(waiting) is not None:
        bad.append("a call completed while it had no token to be tried with")
    h.run(70)
    if h.result(waiting) is None or h.result(waiting).outcome is not mod.Outcome.OK:
        bad.append(f"the trial slot was left taken by a call the budget turned away: {h.result(waiting)}")
    # a ban: the provider's own seconds, then the breaker holds exactly that long
    h = Harness(mod, new_dir(tmp, "ban"), lambda d: response(418, {"Retry-After": "120"}), limit=100)
    cid = h.submit(h.call(deadline=1000))
    h.run(5)
    r = h.result(cid)
    if r is None or r.outcome is not mod.Outcome.FAILED or r.reason != "NOT_RETRYABLE" or r.error.error_class is not E.BANNED:
        bad.append(f"a banned call: {r}")
    if h.governor.banned_until != at(121):
        bad.append(f"the governor was not told about the ban: {h.governor.banned_until}")
    later = h.submit(h.call(symbol="LATER", deadline=1000))
    if h.result(later) is not None:
        bad.append("a call was refused before it was tried")
    h.run(6)
    if h.result(later) is None or h.result(later).outcome is not mod.Outcome.BREAKER_OPEN:
        bad.append(f"a call during a ban: {h.result(later)}")
    h.script = lambda d: ok()
    h.run(121)
    again = h.submit(h.call(symbol="AGAIN", deadline=1000))
    h.run(130)
    if h.result(again) is None or h.result(again).outcome is not mod.Outcome.OK:
        bad.append(f"a call after the ban: {h.result(again)}")
    # a tool that changed shape is shut off alone
    h = Harness(mod, new_dir(tmp, "tool"), lambda d: ok(b"<html>not json</html>") if d.operation == "deep" else ok(), limit=100)
    return bad + _tool_scoped(mod, tmp)


def _tool_scoped(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []

    class Strict(FakeAdapter):
        def classify(self, capture, now):  # type: ignore[no-untyped-def]
            if capture.request.get("operation") == "deep" and b"html" in capture.body:
                return mod.ConnectorError(E.MALFORMED, "not json")
            return super().classify(capture, now)

    h = Harness(
        mod,
        new_dir(tmp, "tool2"),
        lambda d: ok(b"<html>no</html>") if d.operation == "deep" else ok(),
        limit=100,
        adapter=Strict(mod),
        breaker=BreakerConfig(consecutive_failures=2, cooldown_s=30),
    )
    for i in range(2):
        h.submit(h.call(op="deep", symbol=f"D{i}"))
        h.run(5 * (i + 1))
    deep = h.submit(h.call(op="deep", symbol="D9"))
    plain = h.submit(h.call(op="analyze", symbol="A1"))
    h.run(20)
    if h.result(deep) is None or h.result(deep).outcome is not mod.Outcome.BREAKER_OPEN:
        bad.append(f"the tool that changed shape still gets calls: {h.result(deep)}")
    if h.result(plain) is None or h.result(plain).outcome is not mod.Outcome.OK:
        bad.append(f"another tool was shut off with it: {h.result(plain)}")
    return bad


def sharing(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "share"), lambda d: ok(latency=5.0), limit=100)
    a = h.submit(h.call(symbol="sol"))
    b = h.submit(h.call(symbol="SOL", caller="another"))
    c = h.submit(h.call(symbol="ETH"))
    d = h.submit(h.call(symbol="SOL", priority=P.P2, namespace=mod.ON_DEMAND))
    e = h.submit(h.call(op="deep", symbol="SOL"))
    h.run(30)
    if len(h.dispatched) != 4:
        bad.append(f"{len(h.dispatched)} fetches for five calls of which two are identical")
    ra, rb = h.result(a), h.result(b)
    if (
        ra is None
        or rb is None
        or ra.capture_id != rb.capture_id
        or rb.shared_with != a
        or ra.shared_with is not None
        or rb.attempts != 0
        or ra.attempts != 1
    ):
        bad.append(f"identical calls did not share one fetch: {ra} {rb}")
    ids = {h.result(i).capture_id for i in (a, c, d, e)}
    if len(ids) != 4:
        bad.append("calls that differ in arguments, namespace or operation shared a fetch")
    if h.gateway.pending() != 0:
        bad.append("sharing calls were left pending")
    # a waiter whose deadline passes first is released and the fetch carries on
    h = Harness(mod, new_dir(tmp, "waiter"), lambda d: ok(latency=40.0), limit=100)
    first = h.submit(h.call(symbol="SOL", deadline=500))
    waiter = h.submit(h.call(symbol="SOL", deadline=20))
    h.tick_at(0)
    h.tick_at(20)
    if h.result(waiter) is not None:
        bad.append("a waiter was released at its deadline, not after it")
    h.tick_at(20.001)
    rw = h.result(waiter)
    if rw is None or rw.outcome is not mod.Outcome.DEADLINE_UNREACHABLE or rw.reason != "WAITER_DEADLINE" or rw.shared_with != first:
        bad.append(f"a waiter past its deadline: {rw}")
    h.run(30)
    if h.result(first) is not None:
        bad.append("the shared fetch ended early")
    h.run(50)
    if h.result(first) is None or h.result(first).outcome is not mod.Outcome.OK:
        bad.append("the shared fetch did not complete")
    if h.result(waiter).outcome is not mod.Outcome.DEADLINE_UNREACHABLE or h.result(waiter).reason != "WAITER_DEADLINE":
        bad.append("a released waiter was completed a second time")
    if h.gateway.pending() != 0:
        bad.append("a released waiter is still pending")
    # a failure reaches the waiters too
    h = Harness(mod, new_dir(tmp, "waiter2"), lambda d: response(401), limit=100)
    first = h.submit(h.call(symbol="SOL"))
    second = h.submit(h.call(symbol="SOL"))
    h.run(10)
    r2 = h.result(second)
    if (
        r2 is None
        or r2.outcome is not mod.Outcome.FAILED
        or r2.error is None
        or r2.error.error_class is not E.AUTH
        or r2.shared_with != first
    ):
        bad.append(f"a waiter does not share its fetch's failure: {r2}")
    return bad


def cache(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "cache"), lambda d: ok(latency=1.0), limit=100)
    league = h.submit(h.call(symbol="SOL"))
    h.run(5)
    capture = h.result(league).capture_id
    other = h.submit(h.call(symbol="ETH", priority=P.P2, namespace=mod.ON_DEMAND, max_cache_age_s=10))
    if h.result(other) is not None:
        bad.append("an on-demand call read a capture made for other arguments")
    od = h.submit(h.call(symbol="SOL", priority=P.P2, namespace=mod.ON_DEMAND, max_cache_age_s=10))
    r = h.result(od)
    if r is None or not r.from_cache or r.capture_id != capture or r.attempts != 0:
        bad.append(f"an on-demand call did not read a fresh league capture: {r}")
    if len(h.dispatched) != 1:
        bad.append("a cached read went to the provider")
    h.run(40)
    stale = h.submit(h.call(symbol="SOL", priority=P.P2, namespace=mod.ON_DEMAND, max_cache_age_s=10))
    if h.result(stale) is not None:
        bad.append("an on-demand call read a capture older than it allows")
    h.run(45)
    fetched = h.result(stale)
    if fetched is None or fetched.from_cache or fetched.capture_id == capture:
        bad.append(f"a stale cache was not refetched: {fetched}")
    # what on-demand fetched never enters the league cache, and a read without an age never reads
    again = h.submit(h.call(symbol="SOL", priority=P.P2, namespace=mod.ON_DEMAND, max_cache_age_s=30))
    if h.result(again) is not None and h.result(again).capture_id == fetched.capture_id:
        bad.append("an on-demand capture was served as a league capture")
    none = h.submit(h.call(symbol="SOL", priority=P.P2, namespace=mod.ON_DEMAND))
    if h.result(none) is not None and h.result(none).from_cache:
        bad.append("a call that did not ask for a cached read got one")
    # the league never reads on-demand captures or the cache
    h.run(60)
    league2 = h.submit(h.call(symbol="SOL", max_cache_age_s=1000))
    if h.result(league2) is not None and h.result(league2).from_cache:
        bad.append("a league call read the cache")
    # the age is inclusive
    h = Harness(mod, new_dir(tmp, "cache2"), lambda d: ok(latency=0.0), limit=100)
    first = h.submit(h.call(symbol="SOL"))
    h.run(0)
    h.run(10)
    edge = h.submit(h.call(symbol="SOL", priority=P.P2, namespace=mod.ON_DEMAND, max_cache_age_s=10))
    if h.result(edge) is None or not h.result(edge).from_cache or h.result(first) is None:
        bad.append("a capture exactly as old as the limit must still be read")
    h.run(10.5)
    # a failed league fetch is not cached
    h = Harness(mod, new_dir(tmp, "cache3"), lambda d: response(401), limit=100)
    h.submit(h.call(symbol="SOL"))
    h.run(5)
    od = h.submit(h.call(symbol="SOL", priority=P.P2, namespace=mod.ON_DEMAND, max_cache_age_s=100))
    if h.result(od) is not None:
        bad.append("a failure was served from the cache")
    return bad


def floods(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "flood"), lambda d: ok(latency=1.0), limit=1, concurrency=1, queue_cap={P.P2: 3})
    ids = [h.submit(h.call(priority=P.P2, namespace=mod.ON_DEMAND, symbol=f"F{i}", deadline=5000, caller=f"user{i}")) for i in range(6)]
    results = [h.result(i) for i in ids]
    refused = [r for r in results if r is not None]
    if len(refused) != 3 or any(r.outcome is not mod.Outcome.BUDGET_EXHAUSTED or r.queue_position != 3 or r.attempts != 0 for r in refused):
        bad.append(f"a flood of on-demand calls: {results}")
    if h.gateway.queue_depth(P.P2) != 3:
        bad.append("the on-demand queue is not capped")
    # the judge is served while the flood waits
    judge = h.submit(h.call(priority=P.PJ, symbol="JUDGE", deadline=5000))
    h.run(10)
    r = h.result(judge)
    if r is None or r.outcome is not mod.Outcome.OK:
        bad.append(f"the judge was not served during a flood: {r}")
    # other classes are not capped by default configuration
    h = Harness(mod, new_dir(tmp, "nocap"), lambda d: ok(latency=1.0), limit=1, concurrency=1)
    for i in range(150):
        h.submit(h.call(priority=P.P0, symbol=f"L{i}", deadline=100000))
    if any(h.result(i) is not None for i in range(1, 151)):
        bad.append("league calls were capped")
    h = Harness(mod, new_dir(tmp, "defaultcap"), lambda d: ok(latency=1.0), limit=1, concurrency=1)
    for i in range(101):
        h.submit(h.call(priority=P.P2, namespace=mod.ON_DEMAND, symbol=f"X{i}", deadline=100000))
    if h.result(101) is None or h.result(101).outcome is not mod.Outcome.BUDGET_EXHAUSTED or h.result(100) is not None:
        bad.append("the default cap on on-demand calls is not 100")
    return bad


def windows(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "windows"), lambda d: ok(latency=1.0), limit=6, window=60, model=RateModel.TOKEN_BUCKET, concurrency=1)
    window = h.gateway.declare_window("ryo", at(100), 6, at(220))
    early = h.submit(h.call(priority=P.P3, namespace=mod.ON_DEMAND, symbol="EARLY", deadline=1000))
    h.run(50)
    if h.result(early) is None:
        bad.append("background work was held back long before a window")
    h.run(91)
    late = h.submit(h.call(priority=P.P3, namespace=mod.ON_DEMAND, symbol="LATE", deadline=1000))
    mid = h.submit(h.call(priority=P.P2, namespace=mod.ON_DEMAND, symbol="MID", deadline=1000))
    league = [h.submit(h.call(priority=P.P0, symbol=f"W{i}", deadline=600, window=window)) for i in range(6)]
    h.run(219)
    if h.result(late) is not None or h.result(mid) is not None:
        bad.append("background work ran within one refill period of a window or inside it")
    if any(h.result(i) is None or h.result(i).outcome is not mod.Outcome.OK for i in league):
        bad.append("the window's own calls did not run")
    if h.governor.earmarks[0].n != 0:
        bad.append(f"a window's own calls must pay its need down: {h.governor.earmarks[0].n} left")
    h.run(260)
    for name, cid in (("background", late), ("investigation", mid)):
        r = h.result(cid)
        if r is None or r.outcome is not mod.Outcome.OK:
            bad.append(f"{name} work did not resume after the window: {r}")
        elif seconds_of(r.finished_at) <= 220:
            bad.append(f"{name} work finished before the window ended")
    h.gateway.end_window("ryo", window)
    if h.governor.earmarks:
        bad.append("a window was not ended")
    # follow-refresh work may not take the window's tokens
    h = Harness(mod, new_dir(tmp, "windows2"), lambda d: ok(latency=1.0), limit=6, window=60, model=RateModel.TOKEN_BUCKET, concurrency=1)
    h.gateway.declare_window("ryo", at(30), 6, at(100))
    h.run(0)
    for i in range(6):
        h.submit(h.call(priority=P.P0, symbol=f"X{i}", deadline=100, window=None))
    h.run(1)
    refresh = h.submit(h.call(priority=P.P1, symbol="REFRESH", deadline=500))
    h.run(2)
    if h.result(refresh) is not None and seconds_of(h.result(refresh).finished_at) < 100:
        bad.append("follow-refresh work took tokens a declared window needs")
    return bad


def restarts(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    root = new_dir(tmp, "restart")
    h = Harness(mod, root, lambda d: ok(latency=10.0), limit=100)
    done = h.submit(h.call(symbol="DONE"))
    h.run(15)
    killed = [h.submit(h.call(symbol=f"K{i}", op="deep" if i else "analyze")) for i in range(2)]
    h.holds.update(killed)
    h.run(16)
    if len(h.dispatched) != 3:
        bad.append("the killed calls were not dispatched")
    # a new process: same stores, empty queue
    store = CaptureStore(new_dir(tmp, "restart-b") / "unused")
    store.close()
    h.store.close()
    reopened = Harness(mod, root, lambda d: ok(), limit=100)
    abandoned = reopened.gateway.recover(at(20))
    if len(abandoned) != 2:
        bad.append(f"recovery found {len(abandoned)} abandoned dispatches, expected 2")
    for capture_id in abandoned:
        capture = reopened.store.get(capture_id)
        if capture.status is not None or capture.request.get("outcome") != "ABANDONED":
            bad.append("an abandoned dispatch was not recorded as abandoned")
    if reopened.governor.available(at(20)) != 100 - 1 - 3:
        bad.append(f"abandoned dispatches must stay counted: {reopened.governor.available(at(20))} left")
    if reopened.gateway.recover(at(21)) != []:
        bad.append("recovery is not idempotent")
    if reopened.governor.available(at(21)) != 96:
        bad.append("a second recovery charged again")
    if reopened.gateway.pending() != 0 or reopened.dispatched:
        bad.append("an abandoned call was replayed")
    if done is None or len(reopened.store) != 3:
        bad.append(f"the store holds {len(reopened.store)} captures after recovery, expected the finished one and two abandoned")
    # a journal entry for a provider that is not registered is abandoned without a charge; a torn last line was never sent
    other = Harness(mod, new_dir(tmp, "restart-other"), limit=100)
    other.journal.path.write_text(
        '{"attempt":1,"capture_id":"cap-'
        + "a" * 32
        + '","cost":"1.0","operation":"x","priority":"P0","provider":"elsewhere","sent_at":"2026-10-29T12:00:00.000Z"}\n'
        '{"attempt":1,"capture_id":"cap-' + "b" * 32 + '","cost":"1.0","oper'
    )
    if other.journal.entries()[-1]["provider"] != "elsewhere" or len(other.journal.entries()) != 1:
        bad.append("a torn journal line was read")
    if len(other.gateway.recover(at(5))) != 1 or other.governor.available(at(5)) != 100:
        bad.append("an entry for an unknown provider was charged to another")
    # an old dispatch costs nothing in the new window
    old = Harness(mod, new_dir(tmp, "restart-old"), lambda d: ok(latency=10.0), limit=100)
    one = old.submit(old.call(symbol="OLD"))
    old.holds.add(one)
    old.run(1)
    old.store.close()
    later = Harness(mod, new_dir(tmp, "restart-old"), limit=100)
    later.gateway.recover(at(500))
    if later.governor.available(at(500)) != 100:
        bad.append("a dispatch from long before the window was charged to it")
    return bad


def wakeups(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(
        mod, new_dir(tmp, "wake"), lambda d: response(503, {"Retry-After": "5"}, latency=1.0), limit=1, concurrency=2, rng=FixedRandom(0)
    )
    if h.gateway.next_wakeup(at(0)) is not None:
        bad.append("an empty gateway wants to wake up")
    h.submit(h.call(symbol="A", deadline=500))
    h.submit(h.call(symbol="B", deadline=500))
    wake = h.gateway.next_wakeup(at(0))
    if wake != at(0):
        bad.append(f"work that can start now: wake at {wake}")
    h.gateway.tick(at(0))
    wake = h.gateway.next_wakeup(at(0))
    if wake != at(60):
        bad.append(f"a call waiting for the next window wakes at {wake}")
    h2 = Harness(mod, new_dir(tmp, "wake2"), lambda d: response(503, {"Retry-After": "5"}, latency=1.0), limit=100, rng=FixedRandom(0))
    h2.submit(h2.call(symbol="A", deadline=500))
    h2.run(1)
    wake = h2.gateway.next_wakeup(at(1))
    if wake != at(6):
        bad.append(f"a planned retry wakes at {wake}")
    h5 = Harness(mod, new_dir(tmp, "wake5"), limit=100)
    h5.submit(h5.call(symbol="A", deadline=30))
    if h5.gateway.next_wakeup(at(100)) != at(100):
        bad.append(f"a wake-up for work that is already due is {h5.gateway.next_wakeup(at(100))}")
    # a call blocked only by the concurrency cap wakes at its expiry, not at once
    h3 = Harness(mod, new_dir(tmp, "wake3"), lambda d: ok(latency=100.0), limit=100, concurrency=1)
    h3.submit(h3.call(symbol="A", deadline=28))
    h3.submit(h3.call(symbol="B", deadline=30))
    h3.gateway.tick(at(0))
    wake = h3.gateway.next_wakeup(at(0))
    if wake is None or abs(seconds_of(wake) - 25.001) > 1e-6:
        bad.append(f"a call blocked by the concurrency cap wakes at {wake}")
    # a waiter's deadline is a wake-up too
    h4 = Harness(mod, new_dir(tmp, "wake4"), lambda d: ok(latency=100.0), limit=100)
    h4.submit(h4.call(symbol="SOL", deadline=900))
    h4.submit(h4.call(symbol="SOL", deadline=50))
    h4.gateway.tick(at(0))
    if h4.gateway.next_wakeup(at(0)) != at(50.001):
        bad.append(f"a waiter's deadline is not a wake-up: {h4.gateway.next_wakeup(at(0))}")
    return bad


def validation(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    h = Harness(mod, new_dir(tmp, "valid"), lambda d: ok())
    cid = h.submit(h.call())
    dispatches = h.gateway.tick(at(0))
    d = dispatches[0]
    other = mod.Dispatch(d.call_id, d.provider, d.operation, d.args, "cap-" + "e" * 32, d.timeout_s, d.attempt, d.started_at)
    from hoshitori.capture import RawCapture

    wrong = RawCapture("cap-" + "e" * 32, "ryo", {}, 200, {}, b"", at(0), at(1), None)
    for label, dispatch, outcome in [
        ("a capture that is not the dispatched one", d, wrong),
        ("a dispatch that was never made", other, wrong),
    ]:
        try:
            h.gateway.finish(dispatch, at(1), outcome)
            bad.append(f"{label}: accepted")
        except mod.GatewayError:
            pass
    right = RawCapture(d.capture_id, "ryo", {}, 200, {}, b"{}", at(0), at(1), None)
    h.gateway.finish(d, at(1), right)
    try:
        h.gateway.finish(d, at(2), right)
        bad.append("a dispatch was finished twice")
    except mod.GatewayError:
        pass
    if h.result(cid).outcome is not mod.Outcome.OK:
        bad.append("the valid finish was not accepted")
    try:
        h.gateway.submit(h.call(), at(0)) if False else h.gateway._state("nobody")
        bad.append("an unknown provider has a state")
    except mod.GatewayError:
        pass
    return bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "basic": basic,
    "refusals": refusals,
    "deadlines": deadlines,
    "rate": rate,
    "ordering": ordering,
    "concurrency": concurrency,
    "retries": retries,
    "breakers": breakers,
    "sharing": sharing,
    "cache": cache,
    "floods": floods,
    "windows": windows,
    "restarts": restarts,
    "wakeups": wakeups,
    "validation": validation,
}


def failures(mod: types.ModuleType, tmp: Path, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
