"""Checks of the registry triggers (``hoshitori.triggers``), runnable against a mutant."""

from __future__ import annotations

import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import evidence, manifest
from hoshitori.ids import natural_id
from hoshitori.registry import Registry
from hoshitori.timeline import to_ms
from tests.registry_checks import DELISTED, HALTED, PERP, T0, TRADING, inst, lst, t

D = Decimal
BASE = to_ms(T0)
BINANCE = evidence.Family(evidence.FamilyKind.EXCHANGE, "binance")
OKX = evidence.Family(evidence.FamilyKind.EXCHANGE, "okx")


def ms(seconds: float) -> int:
    return to_ms(t(seconds))


def spot(raw: str = "BTCUSDT", base: str = "BTC", **over: Any) -> Any:
    return inst("binance-spot", raw, base, **over)


def admit(reg: Registry, listing: Any, at: float, scope: str = "spot") -> None:
    reg.admit(listing, scope=scope, known_at=t(at), capture_id=f"cap-{at}")


def three(*extra: Any) -> Any:
    return lst("binance-spot", spot(), spot("ETHUSDT", "ETH"), spot("SOLUSDT", "SOL"), *extra)


def window(mod: types.ModuleType, reg: Registry, after: int = BASE - 1, until: int = BASE + 10**6, impact: Any = None) -> Any:
    return mod.registry_triggers(reg, after_ms=after, until_ms=until, impact=impact or mod.equal_impact)


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
        except self.mod.TriggerError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "rules",
        (mod.NEW, mod.REMOVED, mod.STATUS, mod.RULE_VERSION),
        ("REGISTRY_NEW_INSTRUMENT", "REGISTRY_INSTRUMENT_REMOVED", "REGISTRY_STATUS_CHANGED", "1"),
    )
    c.eq("the window ends at the year 9999", mod.MAX_INSTANT_MS, 253_402_300_799_999)
    c.eq("a change is frozen", mod.Change.__dataclass_params__.frozen, True)
    c.eq("the equal impact", mod.equal_impact(None), D(1))
    return c.bad


def check_baseline_and_diff(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    admit(reg, three(), 0)
    c.eq("the first list is the baseline: no triggers", window(mod, reg), ())
    admit(reg, three(spot("DOGEUSDT", "DOGE")), 60)
    got = window(mod, reg)
    c.eq("one new instrument", len(got), 1)
    trig = got[0]
    iid = spot("DOGEUSDT", "DOGE").instrument_id
    c.eq(
        "its fields",
        (trig.rule, trig.rule_version, trig.symbol, trig.primary, trig.families, trig.first_seen_at_ms, trig.impact),
        (mod.NEW, "1", "DOGE", True, frozenset({BINANCE}), ms(60), D(1)),
    )
    c.eq("its id", trig.trigger_id, natural_id("tg", {"rule": mod.NEW, "venue": "binance-spot", "instrument": iid, "known_at_ms": ms(60)}))
    c.eq("it qualifies for the manifest on one venue", trig.qualified(), True)
    admit(reg, lst("binance-spot", spot(), spot("ETHUSDT", "ETH"), spot("DOGEUSDT", "DOGE")), 120)
    removed = [x for x in window(mod, reg) if x.rule == mod.REMOVED]
    c.eq(
        "one removed instrument",
        [(x.symbol, x.first_seen_at_ms, x.primary, x.families) for x in removed],
        [("SOL", ms(120), True, frozenset({BINANCE}))],
    )
    c.eq(
        "its id is of the removed instrument",
        removed[0].trigger_id,
        natural_id(
            "tg", {"rule": mod.REMOVED, "venue": "binance-spot", "instrument": spot("SOLUSDT", "SOL").instrument_id, "known_at_ms": ms(120)}
        ),
    )
    c.eq("everything in time order", [(x.rule, x.symbol) for x in window(mod, reg)], [(mod.NEW, "DOGE"), (mod.REMOVED, "SOL")])
    # a returned instrument is news again
    admit(reg, three(spot("DOGEUSDT", "DOGE")), 180)
    c.eq(
        "the same instrument added again",
        [(x.rule, x.symbol, x.first_seen_at_ms) for x in window(mod, reg) if x.first_seen_at_ms == ms(180)],
        [(mod.NEW, "SOL", ms(180))],
    )
    return c.bad


def check_status_changes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    admit(reg, three(), 0)
    admit(reg, lst("binance-spot", spot(), spot("ETHUSDT", "ETH", status=HALTED), spot("SOLUSDT", "SOL", tick="0.5")), 60)
    got = window(mod, reg)
    c.eq(
        "a halt is a trigger and a tick-size change is not",
        [(x.rule, x.symbol, x.first_seen_at_ms) for x in got],
        [(mod.STATUS, "ETH", ms(60))],
    )
    seen: list[Any] = []

    def spy(change: Any) -> Decimal:
        seen.append(change)
        return D(3)

    window(mod, reg, impact=spy)
    c.eq(
        "the impact function sees the change",
        [(x.rule, x.venue, x.scope, x.symbol, x.known_at_ms, x.previous_status, x.instrument.status) for x in seen],
        [(mod.STATUS, "binance-spot", "spot", "ETH", ms(60), TRADING, HALTED)],
    )
    admit(reg, lst("binance-spot", spot(), spot("ETHUSDT", "ETH", status=DELISTED), spot("SOLUSDT", "SOL", tick="0.5")), 120)
    later = [(x.rule, x.symbol, x.first_seen_at_ms) for x in window(mod, reg) if x.first_seen_at_ms == ms(120)]
    c.eq("a later status change", later, [(mod.STATUS, "ETH", ms(120))])
    seen.clear()
    window(mod, reg, impact=spy)
    c.eq("the previous status is the one before", [x.previous_status for x in seen if x.known_at_ms == ms(120)], [HALTED])
    return c.bad


def check_window(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    admit(reg, three(), 0)
    admit(reg, three(spot("DOGEUSDT", "DOGE")), 60)
    admit(reg, three(spot("DOGEUSDT", "DOGE"), spot("XRPUSDT", "XRP")), 120)
    syms = lambda a, u: [x.symbol for x in window(mod, reg, after=a, until=u)]  # noqa: E731
    c.eq("the whole window", syms(ms(0), ms(120)), ["DOGE", "XRP"])
    c.eq("after is exclusive", syms(ms(60), ms(120)), ["XRP"])
    c.eq("one millisecond before is in", syms(ms(60) - 1, ms(60)), ["DOGE"])
    c.eq("until is inclusive", syms(ms(0), ms(60)), ["DOGE"])
    c.eq("one millisecond short of it is out", syms(ms(0), ms(60) - 1), [])
    c.eq("an empty window", syms(ms(60), ms(60)), [])
    c.eq("a window after everything", syms(ms(120), ms(500)), [])
    # removals respect the window too
    admit(reg, three(spot("DOGEUSDT", "DOGE")), 180)
    c.eq(
        "a removal is in a window that holds it",
        [(x.rule, x.symbol) for x in window(mod, reg, after=ms(120), until=ms(180))],
        [(mod.REMOVED, "XRP")],
    )
    c.eq("and out of one that does not", [x.symbol for x in window(mod, reg, after=ms(180), until=ms(500))], [])
    # a change before the window still moves the state a later one is measured against
    admit(reg, lst("binance-spot", spot(), spot("ETHUSDT", "ETH", status=HALTED), spot("SOLUSDT", "SOL"), spot("DOGEUSDT", "DOGE")), 240)
    admit(
        reg,
        lst("binance-spot", spot(), spot("ETHUSDT", "ETH", status=HALTED), spot("SOLUSDT", "SOL"), spot("DOGEUSDT", "DOGE", status=HALTED)),
        300,
    )
    c.eq("a second change reports the status before it", [x.symbol for x in window(mod, reg, after=ms(240), until=ms(300))], ["DOGE"])
    return c.bad


def check_rejected_and_baselines(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    admit(reg, lst("binance-spot"), 0)  # empty: rejected
    c.eq("a rejected first list produces nothing", window(mod, reg), ())
    admit(reg, three(), 30)
    c.eq("and the first accepted list is still the baseline", window(mod, reg), ())
    admit(reg, lst("binance-spot", spot(), inst("okx-spot", "BTC-USDT", "BTC")), 60)  # a foreign instrument: rejected
    c.eq("a rejected later list produces nothing", window(mod, reg), ())
    admit(reg, three(spot("DOGEUSDT", "DOGE")), 90)
    c.eq(
        "the next accepted list is a diff against the last accepted one",
        [(x.rule, x.symbol, x.first_seen_at_ms) for x in window(mod, reg)],
        [(mod.NEW, "DOGE", ms(90))],
    )
    # another venue and scope has a baseline of its own
    admit(reg, lst("okx-spot", inst("okx-spot", "BTC-USDT", "BTC"), inst("okx-spot", "XRP-USDT", "XRP")), 120)
    c.eq("another venue's first list is a baseline too", [x.first_seen_at_ms for x in window(mod, reg)], [ms(90)])
    admit(
        reg,
        lst("okx-spot", inst("okx-spot", "BTC-USDT", "BTC"), inst("okx-spot", "XRP-USDT", "XRP"), inst("okx-spot", "ADA-USDT", "ADA")),
        150,
    )
    got = [(x.symbol, x.families) for x in window(mod, reg) if x.first_seen_at_ms == ms(150)]
    c.eq("and then it reports as another operator", got, [("ADA", frozenset({OKX}))])
    perps = lst("binance-usdm", inst("binance-usdm", "BTCUSDT", "BTC", kind=PERP), inst("binance-usdm", "ETHUSDT", "ETH", kind=PERP))
    admit(reg, perps, 180, scope="perpetual")
    c.eq("a scope of its own has its own baseline", [x.first_seen_at_ms for x in window(mod, reg)], [ms(90), ms(150)])
    admit(
        reg,
        lst(
            "binance-usdm",
            inst("binance-usdm", "BTCUSDT", "BTC", kind=PERP),
            inst("binance-usdm", "ETHUSDT", "ETH", kind=PERP),
            inst("binance-usdm", "SOLUSDT", "SOL", kind=PERP),
        ),
        210,
        scope="perpetual",
    )
    got = [
        (
            x.symbol,
            x.families,
            x.trigger_id
            == natural_id(
                "tg",
                {
                    "rule": mod.NEW,
                    "venue": "binance-usdm",
                    "instrument": inst("binance-usdm", "SOLUSDT", "SOL", kind=PERP).instrument_id,
                    "known_at_ms": ms(210),
                },
            ),
        )
        for x in window(mod, reg)
        if x.first_seen_at_ms == ms(210)
    ]
    c.eq("two venues of one operator are one family", got, [("SOL", frozenset({BINANCE}), True)])
    return c.bad


def check_symbols_and_impact(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    admit(reg, three(), 0)
    admit(reg, three(inst("binance-spot", "1000PEPEUSDT", "1000PEPE", mult="1000"), spot("abcusdt", "abc")), 60)
    got = window(mod, reg)
    c.eq("a multiplier prefix is the token's own symbol and the symbol is upper case", sorted(x.symbol for x in got), ["ABC", "PEPE"])
    # impact orders under the cap: the function is what is called and its value is carried
    impact = lambda ch: D(10) if ch.symbol == "PEPE" else D(-1)  # noqa: E731
    c.eq("the impact is carried", {x.symbol: x.impact for x in window(mod, reg, impact=impact)}, {"PEPE": D(10), "ABC": D(-1)})
    c.eq("same time, order by trigger id", [x.trigger_id for x in got] == sorted(x.trigger_id for x in got), True)
    c.refuses("an impact that is a float", lambda: window(mod, reg, impact=lambda ch: 1.5), "an impact is a finite decimal")
    c.refuses("an impact that is not a number", lambda: window(mod, reg, impact=lambda ch: D("NaN")), "an impact is a finite decimal")
    c.refuses("an impact that is infinite", lambda: window(mod, reg, impact=lambda ch: D("Infinity")), "an impact is a finite decimal")
    return c.bad


def check_ordering_with_many(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    names = [f"T{n:02d}" for n in range(14)]
    admit(reg, three(), 0)
    admit(reg, three(*[spot(f"{n}USDT", n) for n in names]), 60)
    got = window(mod, reg)
    c.eq(
        "fourteen at one instant: ordered by id, none lost",
        ([x.trigger_id for x in got] == sorted(x.trigger_id for x in got), sorted(x.symbol for x in got)),
        (True, sorted(names)),
    )
    admit(
        reg, three(*[spot(f"{n}USDT", n) for n in names], spot("LATEUSDT", "LATE")), 30 + 60
    )  # earlier than the last record: moved to the next free millisecond
    ordered = [x.first_seen_at_ms for x in window(mod, reg)]
    c.eq("time order across records", ordered == sorted(ordered), True)
    return c.bad


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    admit(reg, three(), 0)
    bad = "is a whole number of milliseconds up to the end of the year 9999"
    c.refuses("a negative after", lambda: window(mod, reg, after=-1), f"after {bad}")
    c.refuses("a boolean after", lambda: window(mod, reg, after=True), f"after {bad}")
    c.refuses("a text after", lambda: window(mod, reg, after="0"), f"after {bad}")
    c.refuses("an after past the year 9999", lambda: window(mod, reg, after=mod.MAX_INSTANT_MS + 1), f"after {bad}")
    c.refuses("a negative until", lambda: window(mod, reg, until=-1), f"until {bad}")
    c.refuses("an until past the year 9999", lambda: window(mod, reg, until=mod.MAX_INSTANT_MS + 1), f"until {bad}")
    c.refuses("a window that ends before it starts", lambda: window(mod, reg, after=10, until=9), "the window starts no later than it ends")
    c.eq("a window of the year 9999's last millisecond", window(mod, reg, after=0, until=mod.MAX_INSTANT_MS), ())
    c.eq("the epoch", window(mod, reg, after=0, until=0), ())
    return c.bad


def check_replay_and_manifest(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = Registry()
    admit(reg, three(), 0)
    admit(reg, three(spot("DOGEUSDT", "DOGE")), 60)
    admit(reg, lst("binance-spot", spot(), spot("ETHUSDT", "ETH", status=HALTED), spot("DOGEUSDT", "DOGE")), 120)
    again = Registry.replay(reg.records())
    c.eq("a replayed registry gives the same triggers", window(mod, again), window(mod, reg))
    c.eq("and the records are not changed by reading", reg.records(), again.records())
    trigs = window(mod, reg)
    resolve = lambda s: manifest.Resolved("tk-" + s.lower(), True, True)  # noqa: E731
    inputs = manifest.Inputs(
        "cy-1", "a" * 64, BASE, BASE + 5_000, manifest.Failed("not used here"), None, trigs, manifest.Capture("cap-log", "5" * 64), ()
    )
    rules = manifest.Rules("s1", "1" * 64, 10, "e1", "2" * 64, 5, "3" * 64)
    built = manifest.build(inputs, rules, resolve)
    c.eq("every registry trigger can enter a manifest", sorted(e.symbol for e in built.union()), ["DOGE", "ETH", "SOL"])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_baseline_and_diff,
        check_status_changes,
        check_window,
        check_rejected_and_baselines,
        check_symbols_and_impact,
        check_ordering_with_many,
        check_validation,
        check_replay_and_manifest,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
