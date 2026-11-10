"""Checks of the practice pass (``hoshitori.practice_job``), runnable against a mutant: the bars are read from the loopback exchange of the
end-to-end league, through the real gateway and the real adapters, and the answer is compared with the exchange's own price formula."""

from __future__ import annotations

import atexit
import copy
import dataclasses
import functools
import shutil
import tempfile
import types
from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import book, practice, practice_record, scheduler, settle_job
from hoshitori.registry import Registry
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import format_rfc3339_ms
from tests.e2e_league import MINUTE, League, league, price
from tests.registry_checks import HALTED, inst, lst

D = Decimal


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


@functools.cache
def started() -> tuple[League, list[dict[str, Any]], int, int]:
    """A league that has run one cycle and then let four minutes pass; kept for the life of the process."""
    stack = ExitStack()
    atexit.register(stack.close)
    directory = Path(tempfile.mkdtemp(prefix="practice-job-"))
    stack.callback(shutil.rmtree, directory, ignore_errors=True)  # last to run: the league closes before its files go
    lg = stack.enter_context(league(directory))
    first = scheduler.tick(lg.services, lg.config)
    assert first.ran is not None and first.ran.slots[0].origin_ms is not None
    lg.clock.jump(4 * 60)
    return lg, scheduler.read_all(lg.services), first.ran.slots[0].origin_ms, to_ms(lg.clock.now())


class Listening:
    """The runner of the league, noting every call made through it."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Any] = []

    def submit(self, call: Any) -> Any:
        self.calls.append(call)
        return self.inner.submit(call)


def settle_services(lg: League, registry: Registry | None = None, runner: Any = None) -> Any:
    env = lg.services.env
    env = env if registry is None else dataclasses.replace(env, registry=registry)
    env = env if runner is None else dataclasses.replace(env, runner=runner)
    return settle_job.SettleServices(lg.services.protocol, env, lg.services.source_urn)


def check_last_closed_minute(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for now, want in ((120_000, 60_000), (120_001, 60_000), (179_999, 60_000), (180_000, 120_000), (60_000, 0)):
        c.eq(f"the latest closed bar at {now}", mod.last_closed_minute(now), want)
    return c.bad


def check_pass(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    lg, records, origin_ms, now = started()
    heard = Listening(lg.services.env.runner)
    got = mod.practice_pass(settle_services(lg, runner=heard), [practice.Config()], records, now, wait_s=20)[0]
    c.eq(
        "the calls are the practice pass's, first-class league calls",
        ({x.caller for x in heard.calls}, {x.priority.value for x in heard.calls}),
        ({"practice"}, {"P0"}),
    )
    c.eq(
        "one clock reading and the candles of the one token on its primary venue",
        ({x.provider for x in heard.calls}, sorted({x.operation for x in heard.calls})),
        ({"binance-spot"}, ["candles", "time"]),
    )
    entries = [practice_record.from_record(r) for r in got.records]
    kinds = sorted(type(e.event).__name__ for e in entries)
    c.eq("three books opened, three BTC forecasts bought in each, three marks", kinds, ["Buy"] * 9 + ["Mark"] * 3 + ["Open"] * 3)
    c.eq("the records are the plan's", got.records, got.plan.records)
    c.eq("nothing unread", got.unread, ())
    c.eq("the pass is frozen", mod.Passed.__dataclass_params__.frozen, True)
    base = [e.event for e in entries if e.name == "L0:BASE" and isinstance(e.event, book.Buy)]
    stamp = next(r["body"]["eligible_after"] for r in records if r["type"] == "forecast_state" and "eligible_after" in r["body"])
    fill_open = (
        -(-max(to_ms(datetime.fromisoformat(stamp)) + 15_000, origin_ms) // MINUTE) * MINUTE
    )  # authorized + latency, never before T0
    c.eq(
        "filled at the exchange's open of the fill bar plus 5 bps",
        {b.price for b in base},
        {price("BTC", fill_open, lg.exchange.anchor) * (1 + D("0.0005"))},
    )
    last = mod.last_closed_minute(now)
    marks = [e.event for e in entries if e.name == "L0:BASE" and isinstance(e.event, book.Mark)]
    c.eq(
        "marked at the close of the latest closed bar",
        [m.prices for m in marks],
        [(("BTC", price("BTC", last, lg.exchange.anchor)),)],
    )
    # before the fill bar has closed there is nothing to read, and nothing is decided
    early = mod.practice_pass(settle_services(lg), [practice.Config(levels=("BASE",))], records, fill_open + 30_000, wait_s=20)[0]
    c.eq(
        "before the fill bar has closed: the books open, three purchases wait, BTC is unread",
        ([type(practice_record.from_record(r).event).__name__ for r in early.records], len(early.plan.waiting), early.unread),
        (["Open"], 3, ("BTC",)),
    )
    # a market that is halted now still has the candles of the minutes that matter (the bars are of the past)
    reg = copy.deepcopy(lg.services.env.registry)
    reg.admit(
        lst("binance-spot", inst("binance-spot", "BTCUSDT", "BTC", status=HALTED), inst("binance-spot", "ETHUSDT", "ETH")),
        scope="spot",
        known_at=from_ms(now - 1_000),
        capture_id="cap-halt",
    )
    halted = mod.practice_pass(settle_services(lg, reg), [practice.Config(levels=("BASE",))], records, now, wait_s=20)[0]
    c.eq(
        "the halted market is read all the same",
        ("Buy" in {type(practice_record.from_record(r).event).__name__ for r in halted.records}, halted.unread),
        (True, ()),
    )
    # a registry that knows no asset has no oracle pair: nothing is read and the token is named
    none = mod.practice_pass(settle_services(lg, Registry()), [practice.Config(levels=("BASE",))], records, now, wait_s=20)[0]
    c.eq(
        "no oracle pair: nothing bought, the token named once",
        ([type(practice_record.from_record(r).event).__name__ for r in none.records], none.unread),
        (["Open"], ("BTC",)),
    )
    c.eq("and the entries wait", len(none.plan.waiting), 3)
    return c.bad


DAY_MS = 86_400_000


def check_clusters_and_daily(mod: types.ModuleType) -> list[str]:
    """The daily closes the cluster cap reads: finished days only, asked once a day, and an unreadable answer leaves the token out."""
    c = Case(mod)
    lg, records, _, now = started()
    env = lg.services.env
    today = now // DAY_MS * DAY_MS
    daily = settle_job.read_daily_closes(env, {("binance-spot", "BTCUSDT"): "BTC"}, now, 31, 20, "practice")
    c.eq("one token", sorted(daily), ["BTC"])
    c.eq("thirty-one finished days, none of today", sorted(daily["BTC"]), [today - n * DAY_MS for n in range(31, 0, -1)])
    c.eq(
        "each close is the exchange's",
        all(close == price("BTC", day, lg.exchange.anchor, DAY_MS) for day, close in daily["BTC"].items()),
        True,
    )
    c.eq(
        "a venue that is not connected leaves its tokens out",
        settle_job.read_daily_closes(env, {("nowhere", "BTCUSDT"): "BTC"}, now, 31, 20, "p"),
        {},
    )
    # the pass asks for the daily closes once for a day and a set of tokens
    heard = Listening(env.runner)
    sv = settle_services(lg, runner=heard)
    sv.env.day_clusters.clear()
    mod.practice_pass(sv, [practice.Config(levels=("BASE",))], records, now, wait_s=20)
    first = [x for x in heard.calls if x.operation == "candles" and dict(x.args).get("interval_s") == 86400]
    mod.practice_pass(sv, [practice.Config(levels=("BASE",))], records, now + MINUTE, wait_s=20)
    second = [x for x in heard.calls if x.operation == "candles" and dict(x.args).get("interval_s") == 86400]
    c.eq("daily closes asked once, not on every pass", (len(first), len(second)), (1, 1))
    c.eq(
        "the day and the token are the key of what was found",
        [(k[0], k[1]) for k in sv.env.day_clusters],
        [(today // DAY_MS, (("binance-spot", "BTCUSDT", "BTC"),))],
    )
    c.eq("one token has no cluster", list(sv.env.day_clusters.values()), [()])
    ask = first[0]
    c.eq(
        "thirty-one days, for the practice pass",
        (str(ask.args["end"]), str(ask.args["start"]), ask.caller),
        (format_rfc3339_ms(from_ms(today)), format_rfc3339_ms(from_ms(today - 31 * DAY_MS)), "practice"),
    )
    mod.practice_pass(sv, [practice.Config(levels=("BASE",))], records, now + DAY_MS, wait_s=20)
    c.eq(
        "the next day asks again and forgets the day before",
        [k[0] for k in sv.env.day_clusters],
        [today // DAY_MS + 1],
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_last_closed_minute, check_pass, check_clusters_and_daily):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1200:]}")
    return bad
