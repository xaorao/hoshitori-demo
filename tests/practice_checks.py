"""Checks of the practice decisions (``hoshitori.practice``), runnable against a mutant.

The ledger is a real one: a league cycle run once on loopback (see ``e2e_league``), whose forecasts, features evidence and lifecycle
records the plan reads. The bars are the fake exchange's own prices, so every number below can be recomputed from the formula the exchange
uses.
"""

from __future__ import annotations

import functools
import tempfile
import types
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import book, canonical, forecast, lifecycle, practice_record, scheduler
from hoshitori.risk_kernel import Limits
from hoshitori.settlement import VOID_REASONS, Bar
from hoshitori.timeline import to_ms
from tests.e2e_league import MINUTE, league, price

D = Decimal
SOURCE = "urn:hoshitori:practice-test"
HOUR = 60 * MINUTE


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.PracticeError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


class World:
    """One committed cycle, kept: its ledger records, the origin and the exchange's price function."""

    def __init__(self, records: list[dict[str, Any]], origin_ms: int, anchor: int) -> None:
        self.records = records
        self.origin_ms = origin_ms
        self.anchor = anchor

    def bars(
        self,
        upto_ms: int,
        *,
        crash_after_ms: int | None = None,
        spikes: dict[int, tuple[Decimal, Decimal]] | None = None,
        only: tuple[str, ...] = ("BTC", "ETH"),
        drop: dict[str, set[int]] | None = None,
    ) -> dict[str, dict[int, Bar]]:
        """One-minute bars from two hours before the origin to ``upto_ms``.

        After ``crash_after_ms`` the price falls 200 a minute; ``spikes`` widens one bar (its high up, its low down); ``only`` leaves out the
        other symbols; ``drop`` removes bars.
        """
        out: dict[str, dict[int, Bar]] = {}
        for symbol in only:
            series: dict[int, Bar] = {}
            t = self.origin_ms - 120 * MINUTE
            while t <= upto_ms:
                o, c = price(symbol, t, self.anchor), price(symbol, t + MINUTE, self.anchor)
                if crash_after_ms is not None and t >= crash_after_ms:
                    drop_by = D(200) * ((t - crash_after_ms) // MINUTE + 1)
                    o, c = o - drop_by + D(200), c - drop_by
                up, down = (spikes or {}).get(t, (D(0), D(0)))
                if t not in (drop or {}).get(symbol, set()):
                    series[t] = Bar(t, o, max(o, c) + up, min(o, c) - down, c)
                t += MINUTE
            out[symbol] = series
        return out

    @property
    def fill_open_ms(self) -> int:
        """The open of the bar the purchases are filled in: the later of the eligibility plus the latency and the origin, to the next bar."""
        stamp = next(r["body"]["eligible_after"] for r in self.records if r["type"] == "forecast_state" and "eligible_after" in r["body"])
        eligible = to_ms(datetime.fromisoformat(stamp))
        return -(-max(eligible + 15_000, self.origin_ms) // MINUTE) * MINUTE


def tampered(records: list[dict[str, Any]], change: Callable[[dict[str, Any]], None]) -> list[dict[str, Any]]:
    """A deep copy of the ledger's records with ``change`` applied to each."""
    out = [canonical.parse_record(canonical.dumps(r)) for r in records]
    for record in out:
        change(record)
    return out


@functools.cache
def world() -> World:
    with tempfile.TemporaryDirectory() as directory, league(Path(directory)) as lg:
        first = scheduler.tick(lg.services, lg.config)
        assert first.ran is not None and first.ran.slots[0].origin_ms is not None
        records = [canonical.parse_record(lg.ledger.read_record(i)) for i in range(lg.ledger.size)]
        return World(records, first.ran.slots[0].origin_ms, lg.exchange.anchor)


def run(
    mod: types.ModuleType,
    w: World,
    steps: list[int],
    *,
    cfg: Any = None,
    crash: int | None = None,
    records: Any = None,
    clusters: Any = (),
    **bar_options: Any,
) -> list[Any]:
    """Plan at each moment in turn, appending what each pass wrote to the ledger before the next."""
    records = list(records if records is not None else w.records)
    plans = []
    for when in steps:
        got = mod.plan(records, cfg or mod.Config(), w.bars(when, crash_after_ms=crash, **bar_options), when, SOURCE, clusters)
        records += list(got.records)
        plans.append(got)
    return plans


def entries(plan: Any) -> list[Any]:
    return [practice_record.from_record(r) for r in plan.records]


def check_config(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("defaults", (mod.ARM, mod.REQUEST_WEIGHT, mod.OPENING_EQUITY), ("L0", D("0.02"), D(100_000)))
    cfg = mod.Config()
    c.eq(
        "the default configuration",
        (cfg.arm, cfg.levels, cfg.equity, cfg.request_weight),
        ("L0", ("BASE", "STRESS", "DOUBLE_FEES"), D(100_000), D("0.02")),
    )
    says = "a practice run has an arm and some of the cost levels ('BASE', 'STRESS', 'DOUBLE_FEES'), once each"
    c.refuses("no arm", lambda: mod.Config(arm=""), says)
    c.refuses("no levels", lambda: mod.Config(levels=()), says)
    c.refuses("a level twice", lambda: mod.Config(levels=("BASE", "BASE")), says)
    c.refuses("a level that is not one", lambda: mod.Config(levels=("BASE", "MADE_UP")), says)
    c.eq("one level is allowed", mod.Config(levels=("STRESS",)).levels, ("STRESS",))
    eq = "the opening equity is a positive decimal"
    for bad in (D(0), D(-1), D("NaN"), D("Infinity"), 5):
        c.refuses(f"equity {bad!r}", lambda bad=bad: mod.Config(equity=bad), eq)
    w = "a request is a share of equity above zero and at most one"
    for bad in (D(0), D(-1), D("1.01"), 0.5):
        c.refuses(f"request {bad!r}", lambda bad=bad: mod.Config(request_weight=bad), w)
    c.eq("one is allowed", mod.Config(request_weight=D(1)).request_weight, D(1))
    c.eq(
        "an opening equity of one is allowed, and below it",
        (mod.Config(equity=D(1)).equity, mod.Config(equity=D("0.5")).equity),
        (D(1), D("0.5")),
    )
    for cls in (mod.Config, mod.Plan, mod._Due):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_config_for(mod: types.ModuleType) -> list[str]:
    """The books are configured from the terms of a protocol, every term to its own place."""
    c = Case(mod)
    terms = {
        "equity": Decimal(50000),
        "request_weight": Decimal("0.03"),
        "fee_bps": Decimal(11),
        "slip_bps": Decimal(6),
        "stress_slip_bps": Decimal(26),
        "latency_ms": Decimal(31000),
        "per_token": Decimal("0.04"),
        "gross": Decimal("0.25"),
        "net": Decimal("0.15"),
        "cluster": Decimal("0.09"),
        "halve_at": Decimal("0.04"),
        "flat_at": Decimal("0.06"),
        "halt_at": Decimal("0.11"),
    }
    cfg = mod.config_for("A9", terms)
    c.eq("the arm", cfg.arm, "A9")
    c.eq("every cost level", cfg.levels, mod.LEVELS)
    c.eq("the opening equity", cfg.equity, Decimal(50000))
    c.eq("a request's share", cfg.request_weight, Decimal("0.03"))
    c.eq("the fee", cfg.costs.fee_bps, Decimal(11))
    c.eq("the slippage", cfg.costs.slip_bps, Decimal(6))
    c.eq("the stress slippage", cfg.costs.stress_slip_bps, Decimal(26))
    c.eq("the latency, whole", (cfg.costs.latency_ms, type(cfg.costs.latency_ms)), (31000, int))
    c.eq(
        "the limits, each in its own place",
        (
            cfg.limits.per_token,
            cfg.limits.gross,
            cfg.limits.net,
            cfg.limits.cluster,
            cfg.limits.halve_at,
            cfg.limits.flat_at,
            cfg.limits.halt_at,
        ),
        (Decimal("0.04"), Decimal("0.25"), Decimal("0.15"), Decimal("0.09"), Decimal("0.04"), Decimal("0.06"), Decimal("0.11")),
    )
    return c.bad


def passing(w: World, arm: str, hours: tuple[int, ...]) -> list[dict[str, Any]]:
    """Records in which ``arm`` voluntarily made no call on the slots of the hours given, for every candidate the rules arm forecast."""
    out = list(w.records)
    seq_at = 100_000_000
    for record in w.records:
        if record["type"] != "forecast" or record["body"]["arm_id"] != "L0" or record["body"]["contract"] != "D":
            continue
        body = record["body"]
        if body["horizon_ms"] // 3_600_000 not in hours:
            continue
        fid = forecast.forecast_id(body["cycle_id"], arm, body["asset_id"], "D", body["horizon_ms"])
        steps = [
            lifecycle.Step(lifecycle.State.SCHEDULED, seq_at, contract="D", horizon_ms=body["horizon_ms"]),
            lifecycle.Step(lifecycle.State.CREATED, seq_at + 1),
            lifecycle.Step(lifecycle.State.EVIDENCE_FROZEN, seq_at + 2),
            lifecycle.Step(lifecycle.State.DECIDED, seq_at + 3),
            lifecycle.Step(lifecycle.State.NO_CALL, seq_at + 4, reason="BELOW_MINIMUM_EDGE", level=forecast.Level.C1),
        ]
        for n, step in enumerate(steps, start=1):
            out.append(lifecycle.to_record(lifecycle.Transition(fid, n, step), SOURCE, step.at_ms))
    return out


def check_shadow(mod: types.ModuleType) -> list[str]:
    """A shadow book follows the reference arm's long calls on the slots the other arm passed (contract section 5)."""
    c = Case(mod)
    w = world()
    when = w.origin_ms + 5 * MINUTE
    shadow = mod.Config(arm="L1.shadow", levels=("BASE",), follows="L0", passed_by="L1")
    c.eq("the book of a shadow is named for the arm that passed", mod.practice_record.book_id(shadow.arm, "BASE"), "L1.shadow:BASE")
    nothing = mod.plan(w.records, shadow, w.bars(when), when, SOURCE)
    c.eq("with no pass of L1 there is nothing to follow", (bought(nothing, "L1.shadow:BASE"), mod.demands(w.records, shadow)), ({}, ()))
    own = mod.Config(levels=("BASE",))
    c.eq("and the rules arm's own book is as it was", sorted(bought(mod.plan(w.records, own, w.bars(when), when, SOURCE))), ["BTC"])
    one_hour = passing(w, "L1", (1,))
    got = mod.plan(one_hour, shadow, w.bars(when), when, SOURCE)
    c.eq("one pass of one hour: one hypothetical trade", sorted(bought(got, "L1.shadow:BASE")), ["BTC"])
    c.eq("it is a purchase in the shadow book only", {e.name for e in entries(got)}, {"L1.shadow:BASE"})
    all_hours = passing(w, "L1", (1, 4, 24))
    c.eq(
        "three passes: three trades, each of the rules arm's size",
        len([e for e in entries(mod.plan(all_hours, shadow, w.bars(when), when, SOURCE)) if isinstance(e.event, book.Buy)]),
        3,
    )
    c.eq("the demand is for the followed slots only", len(mod.demands(one_hour, shadow)), 1)
    c.eq("a pass by the arm that is followed is not a pass of another", mod.demands(passing(w, "L2", (1,)), shadow), ())
    for label, kwargs in (
        ("follows without passed_by", {"follows": "L0"}),
        ("passed_by without follows", {"passed_by": "L1"}),
        ("the arm itself", {"follows": "L1", "passed_by": "L1"}),
    ):
        c.refuses(
            label, lambda k=kwargs: mod.Config(**k), "a shadow book names the arm it follows and a different arm whose passes it follows"
        )
    return c.bad


def check_entries(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    before = run(mod, w, [w.origin_ms - MINUTE])[0]
    c.eq("before the origin only the books are opened", sorted(type(e.event).__name__ for e in entries(before)), ["Open"] * 3)
    c.eq("and every LONG forecast of every book is waited for", len(before.waiting), 9)
    c.eq("with its name", sorted({x.rsplit("/", 1)[1] for x in before.waiting}), ["entry"])
    c.eq("the books are opened with the configured equity", {e.event.equity for e in entries(before)}, {D(100_000)})
    c.eq("one per cost level", sorted(e.name for e in entries(before)), ["L0:BASE", "L0:DOUBLE_FEES", "L0:STRESS"])
    got = run(mod, w, [w.origin_ms + 5 * MINUTE])[0]
    by_book: dict[str, list[Any]] = {}
    for e in entries(got):
        by_book.setdefault(e.name, []).append(e)
    c.eq("three books", sorted(by_book), ["L0:BASE", "L0:DOUBLE_FEES", "L0:STRESS"])
    forecast_ids = {r["id"] for r in w.records if r["type"] == "forecast" and r["body"]["p"] != ""}
    for name, items in by_book.items():
        kinds = [type(e.event).__name__ for e in items]
        c.eq(f"{name}: opened, three purchases, one mark", sorted(kinds), ["Buy", "Buy", "Buy", "Mark", "Open"])
        buys = [e for e in items if isinstance(e.event, book.Buy)]
        c.eq(f"{name}: each follows a forecast of the cycle", {b.forecast_id in forecast_ids for b in buys}, {True})
        c.eq(f"{name}: and a different one each", len({b.forecast_id for b in buys}), 3)
        c.eq(f"{name}: all of BTC", {b.event.symbol for b in buys}, {"BTC"})
        c.eq(
            f"{name}: with the digest of the forecast record",
            {b.forecast_digest is not None and len(b.forecast_digest) == 64 for b in buys},
            {True},
        )
        spent = sum(b.event.quantity * b.event.price for b in buys)
        c.eq(f"{name}: the kernel's per-token cap of 5% is used", D("4950") < spent <= D("5000"), True)
        sizes = sorted(b.event.quantity * b.event.price for b in buys)
        c.eq(
            f"{name}: the first two requests get their 2% and the third what is left of the 5%",
            [abs(x - y) < 20 for x, y in zip(sizes, (1000, 2000, 2000), strict=True)],
            [True] * 3,
        )
        c.eq(f"{name}: all at the same price", len({b.event.price for b in buys}), 1)
        mark = next(e for e in items if isinstance(e.event, book.Mark))
        newest = w.bars(w.origin_ms + 5 * MINUTE)["BTC"]
        c.eq(f"{name}: at the close of the newest bar", mark.event.prices, (("BTC", newest[max(newest)].close),))
        c.eq(f"{name}: the mark prices what is held", [s for s, _ in mark.event.prices], ["BTC"])
        c.eq(f"{name}: all at the plan's moment", {e.event.at_ms for e in items}, {w.origin_ms + 5 * MINUTE})
        c.eq(f"{name}: the opening carries the arm and the level", (items[0].event.arm, items[0].event.level), ("L0", name.split(":")[1]))
    base = next(e for e in by_book["L0:BASE"] if isinstance(e.event, book.Buy)).event
    stress = next(e for e in by_book["L0:STRESS"] if isinstance(e.event, book.Buy)).event
    double = next(e for e in by_book["L0:DOUBLE_FEES"] if isinstance(e.event, book.Buy)).event
    c.eq("the stress book pays a worse price", stress.price > base.price, True)
    c.eq(
        "and the double-fees book pays twice the fee rate",
        (double.fee / (double.quantity * double.price)).quantize(D("1E-6")),
        2 * (base.fee / (base.quantity * base.price)).quantize(D("1E-6")),
    )
    # a second pass at the same moment buys nothing, opens nothing and sells nothing
    twice = run(mod, w, [w.origin_ms + 5 * MINUTE, w.origin_ms + 5 * MINUTE])[1]
    c.eq("the same moment twice writes only the mark", sorted(type(e.event).__name__ for e in entries(twice)), ["Mark"] * 3)
    c.eq("and waits for no entry, only for the exits", {x.rsplit("/", 1)[1] for x in twice.waiting}, {"exit"})
    return c.bad


def check_exits(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    plans = run(mod, w, [w.origin_ms + 5 * MINUTE, w.origin_ms + 6 * MINUTE, w.origin_ms + 70 * MINUTE, w.origin_ms + 5 * HOUR])
    marks_only = entries(plans[1])
    c.eq("a minute later only marks", sorted({type(e.event).__name__ for e in marks_only}), ["Mark"])
    c.eq("nothing is missed for good", (plans[0].missed, plans[1].missed, plans[2].missed, plans[3].missed), ((), (), (), ()))
    c.eq("with the three exits still waited for in each book", sorted({x.rsplit("/", 1)[1] for x in plans[1].waiting}), ["exit"])
    c.eq("one per forecast and book", len(plans[1].waiting), 9)
    after_hour = entries(plans[2])
    sells = [e for e in after_hour if isinstance(e.event, book.Sell)]
    c.eq("after the first horizon each book sells once", sorted(e.name for e in sells), ["L0:BASE", "L0:DOUBLE_FEES", "L0:STRESS"])
    for s in sells:
        c.eq(f"{s.name}: the sale follows a forecast", s.forecast_id is not None, True)
        c.eq(f"{s.name}: of BTC", s.event.symbol, "BTC")
    c.eq("the later passes wait for the rest", len(plans[2].waiting) > 0, True)
    final = entries(plans[3])
    c.eq("by five hours the 4 hour and 24 hour purchases are sold too", sum(isinstance(e.event, book.Sell) for e in final), 6)
    everything = [r for p in plans for r in p.records]
    books = mod.books_of(w.records + everything)
    for name, held in books.items():
        state = book.replay(held.events)
        c.eq(f"{name}: nothing is held at the end", state.positions, ())
        c.eq(f"{name}: three purchases and three sales", (len(held.buys), len(held.sold)), (3, 3))
        c.eq(f"{name}: flat, and the equity is the cash (a last mark after the final sale)", state.cash, state.equity)
        c.eq(f"{name}: sold forecasts are the bought ones", set(held.buys) == held.sold, True)
    quiet = run(mod, w, [w.origin_ms + 5 * MINUTE, w.origin_ms + 70 * MINUTE, w.origin_ms + 5 * HOUR, w.origin_ms + 26 * HOUR])[3]
    c.eq("a day later nothing is left to do", (quiet.records, quiet.waiting, quiet.refused), ((), (), ()))
    return c.bad


def check_stop(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    crash = w.origin_ms + 10 * MINUTE
    plans = run(mod, w, [w.origin_ms + 5 * MINUTE, w.origin_ms + 20 * MINUTE], crash=crash)
    bought = {e.forecast_id: e.event for e in entries(plans[0]) if isinstance(e.event, book.Buy) and e.name == "L0:BASE"}
    sold = {e.forecast_id: e.event for e in entries(plans[1]) if isinstance(e.event, book.Sell) and e.name == "L0:BASE"}
    c.eq("a fall through the stop sells all three at once", sorted(sold), sorted(bought))
    for fid, sale in sold.items():
        c.eq("the sale is below the purchase", sale.price < bought[fid].price, True)
        c.eq(
            "by about the stop distance (1.5 x ATR of 30) and not more than a minute's fall beyond it",
            D(40) < bought[fid].price - sale.price < D(300),
            True,
        )
    return c.bad


def check_orders_of_work(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    when = w.origin_ms + 5 * MINUTE
    one = mod.plan(w.records, mod.Config(levels=("STRESS",)), w.bars(when), when, SOURCE)
    c.eq("one configured level writes one book", {e.name for e in entries(one)}, {"L0:STRESS"})
    small = mod.plan(w.records, mod.Config(request_weight=D("0.001")), w.bars(when), when, SOURCE)
    buys = [e.event for e in entries(small) if isinstance(e.event, book.Buy) and e.name == "L0:BASE"]
    c.eq("a smaller request buys less", sum(b.quantity * b.price for b in buys) < D(400), True)
    wide = mod.plan(
        w.records, mod.Config(limits=Limits(per_token=D("0.5"), gross=D("0.5"), net=D("0.5"), cluster=D("0.5"))), w.bars(when), when, SOURCE
    )
    wbuys = [e.event for e in entries(wide) if isinstance(e.event, book.Buy) and e.name == "L0:BASE"]
    c.eq("wider limits buy the full requests (3 x 2%)", D("5900") < sum(b.quantity * b.price for b in wbuys) < D("6000"), True)
    other = mod.plan(w.records, mod.Config(arm="R"), w.bars(when), when, SOURCE)
    c.eq("another arm has no forecasts: only books", sorted(type(e.event).__name__ for e in entries(other)), ["Open"] * 3)
    no_bars = mod.plan(w.records, mod.Config(), {}, when, SOURCE)
    c.eq(
        "with no bars nothing is bought and every entry is waited for",
        (sorted({type(e.event).__name__ for e in entries(no_bars)}), len(no_bars.waiting)),
        (["Open"], 9),
    )
    c.eq("and none is missed yet", no_bars.missed, ())
    stamped = tampered(w.records, lambda r: r.update(committed_at="2026-01-01T00:00:00.000Z"))
    same = mod.plan(stamped, mod.Config(), w.bars(when), when, SOURCE)
    c.eq(
        "records as the ledger returns them, with their commit time, plan the same",
        same,
        mod.plan(w.records, mod.Config(), w.bars(when), when, SOURCE),
    )
    # a forecast without any lifecycle record is not traded
    stripped = [r for r in w.records if r["type"] != "forecast_state"]
    none = mod.plan(stripped, mod.Config(), w.bars(when), when, SOURCE)
    c.eq("a forecast with no lifecycle is not traded", sorted({type(e.event).__name__ for e in entries(none)}), ["Open"])
    # a features record without the ATR leaves the forecast untradable
    no_atr = [r for r in w.records if not (r["type"] == "evidence" and r["body"].get("path") == "features")]
    c.eq(
        "without the ATR nothing is bought",
        sorted({type(e.event).__name__ for e in entries(mod.plan(no_atr, mod.Config(), w.bars(when), when, SOURCE))}),
        ["Open"],
    )
    return c.bad


def long_variant(w: World, symbol: str, p: str, atr: str | None = None) -> list[dict[str, Any]]:
    def change(record: dict[str, Any]) -> None:
        if record["type"] == "forecast" and record["body"]["symbol"] == symbol:
            record["body"]["p"] = p
        if atr is not None and record["type"] == "evidence" and record["body"].get("path") == "features":
            record["body"]["fields"]["atr14"] = atr

    return tampered(w.records, change)


def bought(plan: Any, name: str = "L0:BASE") -> dict[str, Decimal]:
    """The cost of the purchases of a pass in one book, by symbol."""
    out: dict[str, Decimal] = {}
    for e in entries(plan):
        if e.name == name and isinstance(e.event, book.Buy):
            out[e.event.symbol] = out.get(e.event.symbol, D(0)) + e.event.quantity * e.event.price
    return out


def check_decision(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    when = w.origin_ms + 5 * MINUTE
    one = mod.Config(levels=("BASE",))
    for p, want in (("0.520000", True), ("0.519999", False), ("0.500000", False), ("0.480000", False), ("0.990000", True)):
        got = mod.plan(long_variant(w, "ETH", p), one, w.bars(when), when, SOURCE)
        c.eq(f"ETH at p={p}: bought", "ETH" in bought(got), want)
    c.eq("BTC at its own forecast is long", "BTC" in bought(mod.plan(w.records, one, w.bars(when), when, SOURCE)), True)
    for atr, want in (("0", False), ("30.000000000000", True), ("0.000001", True), ("-1", False)):
        got = mod.plan(long_variant(w, "BTC", "0.700000", atr), one, w.bars(when), when, SOURCE)
        c.eq(f"ATR {atr}: bought", "BTC" in bought(got), want)
    # the entry waits for the bar that closes the minute it is filled in
    opened = w.fill_open_ms
    early = mod.plan(w.records, one, w.bars(opened + 2 * MINUTE), opened + MINUTE - 1, SOURCE)
    c.eq("one millisecond before the fill bar has closed, it is waited for", (len(early.waiting), bought(early)), (3, {}))
    on_time = mod.plan(w.records, one, w.bars(opened + 2 * MINUTE), opened + MINUTE, SOURCE)
    c.eq("when it has closed, it is bought", (len(on_time.waiting), "BTC" in bought(on_time)), (0, True))
    return c.bad


def check_missing_bars(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    opened = w.fill_open_ms
    gone = {"BTC": {opened}}
    cfg = mod.Config(levels=("BASE",))
    for label, when, waiting, missed in (
        ("just inside the retry window", opened + mod.RETRY_MS - 1, 3, 0),
        ("at its end", opened + mod.RETRY_MS, 0, 3),
    ):
        got = mod.plan(w.records, cfg, w.bars(when, drop=gone), when, SOURCE)
        c.eq(f"the fill bar is missing, {label}", (len(got.waiting), len(got.missed), bought(got)), (waiting, missed, {}))
        c.eq(f"{label}: named by book, forecast and step", {x.rsplit("/", 1)[1] for x in got.waiting + got.missed}, {"entry"})
    c.eq("the retry window is an hour", mod.RETRY_MS, HOUR)
    # an exit bar that never comes: the first of the three horizons is given up on at its end plus the window
    first = opened + 5 * MINUTE
    end_1h = w.origin_ms + HOUR
    hole = {"BTC": {opened + 2 * MINUTE}}
    for label, when, waiting, missed in (("inside the window", end_1h + HOUR - 1, 3, 0), ("at its end", end_1h + HOUR, 2, 0)):
        records = list(w.records)
        records += list(mod.plan(records, cfg, w.bars(first), first, SOURCE).records)
        got = mod.plan(records, cfg, w.bars(when, drop=hole), when, SOURCE)
        kinds = {x.rsplit("/", 1)[1] for x in got.waiting + got.missed}
        c.eq(f"an exit bar is missing, {label}", (len(got.waiting), len(got.missed), kinds), (waiting, missed, {"exit"}))
        sold = [e for e in entries(got) if isinstance(e.event, book.Sell)]
        c.eq(
            f"an exit bar is missing, {label}: sold at the next bar there is once the window is over (a gap exit)",
            len(sold),
            1 - waiting + 2 if label == "at its end" else 0,
        )
    # a purchase whose forecast has left the lifecycle is reported, never silently kept
    records = list(w.records)
    records += list(mod.plan(records, cfg, w.bars(first), first, SOURCE).records)
    orphaned = [r for r in records if r["type"] != "forecast_state"]
    got = mod.plan(orphaned, cfg, w.bars(first + MINUTE), first + MINUTE, SOURCE)
    c.eq(
        "held without a forecast is missed for the exit",
        (len(got.missed), {x.rsplit("/", 1)[1] for x in got.missed}, got.waiting),
        (3, {"exit"}, ()),
    )
    return c.bad


def check_levels(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    first, later = w.origin_ms + 5 * MINUTE, w.origin_ms + 25 * MINUTE
    at = w.origin_ms + 12 * MINUTE
    cfg = mod.Config(levels=("BASE",))
    for label, spike, sells in (
        ("a dip of 40 stays above the stop (45 below the fill)", (D(0), D(40)), 0),
        ("a dip of 60 goes through it", (D(0), D(60)), 3),
        ("a rise of 40 stays below the target (45 above the fill)", (D(40), D(0)), 0),
        ("a rise of 60 reaches it", (D(60), D(0)), 3),
    ):
        plans = run(mod, w, [first, later], cfg=cfg, spikes={at: spike})
        got = [e for e in entries(plans[1]) if isinstance(e.event, book.Sell)]
        c.eq(label, len(got), sells)
        if sells:
            bars = w.bars(later, spikes={at: spike})["BTC"]
            if spike[1] > spike[0]:  # a stop: sold at the worse of the stop and the next open
                buy = next(e.event for e in entries(plans[0]) if isinstance(e.event, book.Buy))
                stop = buy.price - mod.K_STOP * D(30)
                want = {min(bars[at + MINUTE].open, stop) * (1 - D("0.0005"))}
            else:
                want = {bars[at + MINUTE].open * (1 - D("0.0005"))}
            c.eq(
                f"{label}: sold at the next bar's open less the slippage (a stop at the worse of it and the stop)",
                {e.event.price for e in got},
                want,
            )
            buy_entry = next(e for e in entries(plans[0]) if isinstance(e.event, book.Buy))
            c.eq(
                f"{label}: the purchase records how it was filled",
                (
                    buy_entry.details is not None and buy_entry.details.latency_ms,
                    buy_entry.details is not None and buy_entry.details.exit_kind,
                ),
                (cfg.costs.latency_ms, None),
            )
            kinds = {e.details.exit_kind if e.details else None for e in entries(plans[1]) if isinstance(e.event, book.Sell)}
            c.eq(f"{label}: the sale records why it was made", kinds, {"STOP" if spike[1] > spike[0] else "TARGET"})
            sale = next(e for e in entries(plans[1]) if isinstance(e.event, book.Sell))
            c.eq(
                f"{label}: and the bar that decided it, the bar it was taken from and where the holding began",
                None if sale.details is None else (sale.details.signal_bar_ms, sale.details.fill_bar_ms, sale.details.held_from_ms),
                (at, at + MINUTE, None if buy_entry.details is None else buy_entry.details.fill_bar_ms),
            )
    return c.bad


def check_portfolio(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    when = w.origin_ms + 5 * MINUTE
    both = long_variant(w, "ETH", "0.700000")
    default = mod.plan(both, mod.Config(levels=("BASE",)), w.bars(when), when, SOURCE)
    spent = bought(default)
    c.eq("two tokens, each at the kernel's 5% cap", sorted(spent), ["BTC", "ETH"])
    c.eq("each near 5000", all(D("4900") < v <= D("5000") for v in spent.values()), True)
    marks = [e.event for e in entries(default) if isinstance(e.event, book.Mark)]
    c.eq("one mark, pricing both by symbol", [[s for s, _ in m.prices] for m in marks], [["BTC", "ETH"]])
    tight = mod.Config(levels=("BASE",), limits=Limits(per_token=D("0.05"), gross=D("0.06"), net=D("0.2"), cluster=D("0.1")))
    # the plan does not depend on the caller's decimal context (a low precision once raised InvalidOperation in the sizing)
    import decimal

    for prec in (6, 10, 100):
        with decimal.localcontext() as ctx:
            ctx.prec = prec
            other = mod.plan(both, mod.Config(levels=("BASE",)), w.bars(when), when, SOURCE)
        c.eq(f"the same plan at a precision of {prec}", [e.event for e in entries(other)], [e.event for e in entries(default)])
    # two requests in one pass under a cap that fits one and a little: contract 4.1 processes them by weight and then by symbol, never by the
    # rank the scan gave them, so BTC is sized first and ETH gets what is left
    together = mod.plan(both, tight, w.bars(when), when, SOURCE)
    got = bought(together)
    c.eq(
        "in one pass the first symbol in the alphabet gets its 5% and the second what the 6% cap leaves",
        (D("4900") < got.get("BTC", D(0)) <= D("5000"), D("900") < got.get("ETH", D(0)) < D("1100")),
        (True, True),
    )
    # the order of the requests handed in is not the order they are sized in: ETH listed first, BTC is still sized first
    from types import SimpleNamespace

    def due(symbol: str) -> Any:
        return mod._Due(f"f-{symbol}", "d" * 64, f"a-{symbol}", symbol, 0, 0, 3_600_000, D(1), False)

    empty = SimpleNamespace(equity=D(100_000), positions=(), drawdown=D(0), halted=False)
    sized = mod._sized([due("ETH"), due("BTC")], empty, mod.Config(levels=("BASE",), request_weight=D("0.05"), limits=tight.limits))
    c.eq(
        "listed ETH then BTC, they are sized BTC then ETH",
        [(d.symbol, D("900") < amount <= D("5000")) for d, amount in sized],
        [("BTC", True), ("ETH", True)],
    )
    c.eq("BTC has its full 5% and ETH what the 6% cap leaves", [round(amount) for _, amount in sized], [5000, 1000])
    # ETH first (BTC bars not there yet), then BTC arrives under a gross cap that ETH already uses most of
    eth_only = run(mod, w, [when, when + MINUTE], cfg=tight, records=both, only=("ETH",))
    c.eq(
        "with only ETH bars, ETH is bought and BTC waited for", (sorted(bought(eth_only[0])), len(eth_only[0].waiting) > 0), (["ETH"], True)
    )
    records = both + [r for p in eth_only for r in p.records]
    second = mod.plan(records, tight, w.bars(when + 2 * MINUTE), when + 2 * MINUTE, SOURCE)
    btc = bought(second).get("BTC", D(0))
    c.eq("BTC gets what is left of the 6% gross cap: about 1%, not 5%", D(900) < btc < D(1100), True)
    marks = [e.event for e in entries(second) if isinstance(e.event, book.Mark)]
    c.eq("the mark prices both holdings, by symbol", [[s for s, _ in m.prices] for m in marks], [["BTC", "ETH"]])
    full = mod.Config(levels=("BASE",), limits=Limits(per_token=D("0.05"), gross=D("0.05"), net=D("0.2"), cluster=D("0.1")))
    eth_full = run(mod, w, [when, when + MINUTE], cfg=full, records=both, only=("ETH",))
    none_left = mod.plan(both + [r for p in eth_full for r in p.records], full, w.bars(when + 2 * MINUTE), when + 2 * MINUTE, SOURCE)
    c.eq(
        "with the gross cap used up BTC is not bought and nothing is refused", (bought(none_left).get("BTC"), none_left.refused), (None, ())
    )
    # tokens that move together share one cap: a cluster of BTC and ETH under a cluster cap of 6% gets 5% for BTC and what is left for ETH
    clustered = mod.Config(levels=("BASE",), limits=Limits(per_token=D("0.05"), gross=D("0.30"), net=D("0.2"), cluster=D("0.06")))
    apart = bought(mod.plan(both, clustered, w.bars(when), when, SOURCE))
    c.eq("without a cluster each token has its own 5%", sorted(apart), ["BTC", "ETH"])
    c.eq("and each is near 5000", all(D("4900") < v <= D("5000") for v in apart.values()), True)
    together_c = bought(mod.plan(both, clustered, w.bars(when), when, SOURCE, clusters=[("BTC", "ETH")]))
    c.eq(
        "in one cluster BTC has its 5% and ETH the 1% that the 6% cluster cap leaves",
        (D("4900") < together_c.get("BTC", D(0)) <= D("5000"), D("900") < together_c.get("ETH", D(0)) < D("1100")),
        (True, True),
    )
    other = bought(mod.plan(both, clustered, w.bars(when), when, SOURCE, clusters=[("BTC", "XRP")]))
    c.eq("a cluster of other tokens changes nothing for these two", sorted(other), ["BTC", "ETH"])
    c.eq("and nothing in size", all(D("4900") < v <= D("5000") for v in other.values()), True)
    full_c = mod.Config(levels=("BASE",), limits=Limits(per_token=D("0.05"), gross=D("0.30"), net=D("0.2"), cluster=D("0.05")))
    c.eq(
        "a cluster cap used up by the first member buys nothing more in it",
        sorted(bought(mod.plan(both, full_c, w.bars(when), when, SOURCE, clusters=[("BTC", "ETH")]))),
        ["BTC"],
    )
    held_first = run(mod, w, [when, when + MINUTE], cfg=clustered, records=both, only=("ETH",), clusters=[("BTC", "ETH")])
    after = mod.plan(
        both + [r for p in held_first for r in p.records],
        clustered,
        w.bars(when + 2 * MINUTE),
        when + 2 * MINUTE,
        SOURCE,
        clusters=[("BTC", "ETH")],
    )
    c.eq(
        "what a cluster member already holds counts against the cluster later too: BTC gets about 1% of the 6%",
        D(900) < bought(after).get("BTC", D(0)) < D(1100),
        True,
    )
    # more than the cash can pay: the book refuses the last purchase and the plan says so
    wild = mod.Config(levels=("BASE",), request_weight=D(1), limits=Limits(per_token=D(1), gross=D(1), net=D(1), cluster=D(1)))
    refused = mod.plan(w.records, wild, w.bars(when), when, SOURCE)
    made = len([e for e in entries(refused) if isinstance(e.event, book.Buy)])
    c.eq("a purchase that the cash cannot pay is refused and named", (len(refused.refused) >= 1, made < 3), (True, True))
    tiny = mod.plan(w.records, mod.Config(levels=("BASE",), equity=D("0.001")), w.bars(when), when, SOURCE)
    c.eq("an amount too small to buy a unit is refused", (len(tiny.refused), bought(tiny)), (3, {}))
    return c.bad


def voided(records: list[dict[str, Any]], forecast_id: str, at_ms: int) -> list[dict[str, Any]]:
    """The ledger's records with one forecast's slot ended as void."""
    histories = lifecycle.rebuild([r for r in records if r["type"] == "forecast_state"])
    advanced = lifecycle.advance(histories[forecast_id], lifecycle.Step(lifecycle.State.VOID, at_ms, reason=sorted(VOID_REASONS)[0]))
    return [*records, lifecycle.to_record(advanced[-1], SOURCE, at_ms)]


def btc_forecasts(w: World) -> dict[int, dict[str, Any]]:
    """The cycle's BTC forecast records by horizon in hours."""
    return {r["body"]["horizon_ms"] // HOUR: r for r in w.records if r["type"] == "forecast" and r["body"]["symbol"] == "BTC"}


def check_demands(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    btc = btc_forecasts(w)
    expected = sorted((r["body"]["asset_id"], "BTC", w.fill_open_ms, w.origin_ms + h * HOUR + MINUTE) for h, r in btc.items())

    def asked(records: list[dict[str, Any]], cfg: Any = None) -> list[Any]:
        return sorted((d.asset_id, d.symbol, d.start_ms, d.end_ms) for d in mod.demands(records, cfg or mod.Config()))

    c.eq(
        "each LONG forecast asks for the bars from its fill to the end of its horizon (and ETH, not long, asks for none)",
        asked(w.records),
        expected,
    )
    c.eq("the demand is frozen", mod.Demand.__dataclass_params__.frozen, True)
    one = mod.Config(levels=("BASE",))
    c.eq("the same for one level", asked(w.records, one), expected)
    bought = run(mod, w, [w.origin_ms + 5 * MINUTE], cfg=one)
    c.eq("after the purchases the exits still need the bars", asked(w.records + [r for p in bought for r in p.records], one), expected)
    done = run(mod, w, [w.origin_ms + 5 * MINUTE, w.origin_ms + 5 * HOUR], cfg=one)
    finished = w.records + [r for p in done for r in p.records]
    c.eq("when everything is sold nothing is asked for", asked(finished, one), [])
    c.eq("but a book that has not sold is still waiting for its bars", asked(finished, mod.Config(levels=("BASE", "STRESS"))), expected)
    # an ended slot: no purchase is made, and none is waited for
    gone = voided(w.records, btc[4]["id"], w.origin_ms + 2 * MINUTE)
    c.eq(
        "a voided forecast with no purchase asks for nothing", asked(gone), [e for e in expected if e[3] != w.origin_ms + 4 * HOUR + MINUTE]
    )
    plan = mod.plan(gone, one, w.bars(w.origin_ms + 5 * MINUTE), w.origin_ms + 5 * MINUTE, SOURCE)
    c.eq(
        "and is not bought",
        sorted(forecast_of(e) for e in entries(plan) if isinstance(e.event, book.Buy)),
        sorted({f["id"] for h, f in btc.items() if h != 4}),
    )
    # a voided forecast that was bought is still sold
    ran = run(mod, w, [w.origin_ms + 5 * MINUTE], cfg=one)
    held = w.records + [r for p in ran for r in p.records]
    ended = voided(held, btc[4]["id"], w.origin_ms + 6 * MINUTE)
    c.eq("a voided forecast with a purchase still asks for its bars", asked(ended, one), expected)
    c.eq("and so does one that one book bought and another did not", asked(ended, mod.Config(levels=("BASE", "STRESS"))), expected)
    sold = mod.plan(ended, one, w.bars(w.origin_ms + 5 * HOUR), w.origin_ms + 5 * HOUR, SOURCE)
    c.eq("and is sold at its end", btc[4]["id"] in {forecast_of(e) for e in entries(sold) if isinstance(e.event, book.Sell)}, True)
    return c.bad


def forecast_of(entry: Any) -> str | None:
    return entry.forecast_id


def check_ladder(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = world()
    wide = Limits(per_token=D(1), gross=D(1), net=D(1), cluster=D(1))
    cfg = mod.Config(levels=("BASE",), request_weight=D("0.5"), limits=wide)
    # a stop that cannot be reached and a fall of 200 a minute from the sixth minute: the drawdown climbs the ladder
    records = tampered(
        w.records,
        lambda r: r["body"]["fields"].update(atr14="100000") if r["type"] == "evidence" and r["body"].get("path") == "features" else None,
    )
    crash = w.origin_ms + 6 * MINUTE
    modes = []
    sales: list[Any] = []
    for minute in (5, 8, 9, 10):
        when = w.origin_ms + minute * MINUTE
        got = mod.plan(records, cfg, w.bars(when, crash_after_ms=crash), when, SOURCE)
        records += list(got.records)
        state = book.replay(mod.books_of(records)["L0:BASE"].events)
        modes.append(state.mode.value)
        sales += [e for e in entries(got) if isinstance(e.event, book.Sell)]
    c.eq("the ladder: active, halved, flat, then the book is sold down", modes[:3], ["ACTIVE", "HALVED", "FLAT"])
    c.eq("nothing is sold before the book is flat, and then every purchase held is", len(sales) >= 1 and modes[2] == "FLAT", True)
    bars = w.bars(w.origin_ms + 10 * MINUTE, crash_after_ms=crash)["BTC"]
    sold_at = {e.event.price for e in sales}
    c.eq(
        "at the open of the first bar after the mark that flattened it, less the slippage",
        sold_at,
        {bars[w.origin_ms + 9 * MINUTE].open * (1 - D("0.0005"))},
    )
    held = mod.books_of(records)["L0:BASE"]
    c.eq("a sale follows each purchase it sells", set(held.sold) <= set(held.buys), True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_config,
        check_config_for,
        check_shadow,
        check_entries,
        check_exits,
        check_stop,
        check_orders_of_work,
        check_decision,
        check_missing_bars,
        check_levels,
        check_portfolio,
        check_ladder,
        check_demands,
    ):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1200:]}")
    return bad
