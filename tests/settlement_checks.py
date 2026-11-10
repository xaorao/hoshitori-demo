"""Checks of settlement (``hoshitori.settlement``), runnable against a mutant: the golden cases, then what no case can show."""

from __future__ import annotations

import copy
import types
from decimal import Decimal
from typing import Any

from hoshitori.ids import natural_id
from tests.settlement_golden import golden_failures, load_cases

M, H = 60_000, 3_600_000
ORIGIN = 1_793_311_320_000


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, text: str | None = None) -> None:
        try:
            call()
        except self.mod.SettlementError as exc:
            if text is not None and not str(exc).startswith(text):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to begin {text!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")

    def refuses_exactly(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.SettlementError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def d(price: str) -> Decimal:
    return Decimal(price)


def simple_pair(mod: types.ModuleType, p0: str = "100.00", ph: str = "101.00") -> Any:
    def oracle(venue: str) -> Any:
        bars = {
            ORIGIN - M: mod.Bar(ORIGIN - M, d(p0), d(p0), d(p0), d(p0)),
            ORIGIN + H - M: mod.Bar(ORIGIN + H - M, d(ph), d(ph), d(ph), d(ph)),
        }
        return mod.Oracle(venue, "BTCUSDT", "USDT", d("1"), d("0.01"), ORIGIN + H + M, bars)

    return mod.OraclePair("A", oracle("binance-spot"), oracle("okx"))


def check_golden(mod: types.ModuleType) -> list[str]:
    cases = load_cases()
    bad = golden_failures(mod)
    kinds = {k: sum(1 for c in cases if c["kind"] == k) for k in ("directional", "barrier", "void")}
    if len(cases) < 60 or min(kinds.values()) < 3:
        bad.append(f"the golden set is too small: {len(cases)} cases {kinds}")
    return bad


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S = mod.State
    c.eq("states", [s.value for s in S], ["PENDING", "SETTLED", "VOID", "UNSETTLEABLE", "AMBIGUOUS"])
    c.eq("sides", [s.value for s in mod.Side], ["LONG", "SHORT"])
    c.eq(
        "a price rounds to its tick, half to even",
        [mod._tick(d(p), d("0.01")) for p in ("100.004", "100.005", "100.006", "100.015", "100.025", "99.995")],
        [d("100.00"), d("100.00"), d("100.01"), d("100.02"), d("100.02"), d("100.00")],
    )
    c.eq("with no tick it is as it is", mod._tick(d("100.004"), None), d("100.004"))
    c.eq(
        "a coarse tick",
        (mod._tick(d("103"), d("5")), mod._tick(d("102.5"), d("5")), mod._tick(d("107.5"), d("5"))),
        (d("105"), d("100"), d("110")),
    )
    c.eq(
        "constants",
        (mod.RULE_VERSION, mod.DISAGREEMENT, mod.BASIS_LIMIT, sorted(mod.VOID_REASONS)),
        (
            "settle/1",
            d("0.005"),
            d("0.003"),
            ["VOID_DELISTED", "VOID_GAP", "VOID_OUTAGE", "VOID_REDENOMINATION", "VOID_TIE", "VOID_TOKEN_EVENT"],
        ),
    )
    for cls in (mod.Bar, mod.Oracle, mod.OraclePair, mod.Directional, mod.Barrier, mod.Settlement, mod.Fine, mod._Need):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    ok = mod.Bar(M, d("1"), d("2"), d("0.5"), d("1.5"))
    c.eq("a bar", (ok.open_ms, ok.open, ok.high, ok.low, ok.close), (M, d("1"), d("2"), d("0.5"), d("1.5")))
    mod.Bar(0, d("1"), d("1"), d("1"), d("1"))  # a flat bar at the epoch is allowed
    mod.Bar(M, d("1"), d("2"), d("1"), d("2"))  # the open at the low and the close at the high
    for label, args in (
        ("a bar off the minute", (M + 1, d("1"), d("1"), d("1"), d("1"))),
        ("a bar before the epoch", (-M, d("1"), d("1"), d("1"), d("1"))),
        ("a zero open", (M, d("0"), d("1"), d("0"), d("1"))),
        ("a negative low", (M, d("1"), d("2"), d("-1"), d("1"))),
        ("a zero close", (M, d("1"), d("1"), d("0"), d("0"))),
        ("a zero high", (M, d("1"), d("0"), d("1"), d("1"))),
        ("a high below the close", (M, d("1"), d("1.4"), d("1"), d("1.5"))),
        ("a high below the open", (M, d("1.5"), d("1.4"), d("1"), d("1"))),
        ("a low above the open", (M, d("1"), d("2"), d("1.1"), d("1.5"))),
        ("a low above the close", (M, d("2"), d("2"), d("1.6"), d("1.5"))),
    ):
        c.refuses(label, lambda a=args: mod.Bar(*a))
    c.eq("the volume is unknown unless given", ok.volume, None)
    for volume in ("0", "0.5", "3"):
        c.eq(f"a volume of {volume} is a volume", mod.Bar(M, d("1"), d("1"), d("1"), d("1"), d(volume)).volume, d(volume))
    for label, volume in (("a negative volume", d("-1")), ("a volume of NaN", d("NaN")), ("an infinite volume", d("Infinity"))):
        c.refuses_exactly(label, lambda v=volume: mod.Bar(M, d("1"), d("1"), d("1"), d("1"), v), "a volume is zero or more")
    bar = mod.Bar(M, d("1"), d("1"), d("1"), d("1"))
    for label, args in (
        ("a zero multiplier", ("v", "i", "USDT", d("0"), None, 0, {})),
        ("a negative multiplier", ("v", "i", "USDT", d("-1"), None, 0, {})),
        ("a zero tick", ("v", "i", "USDT", d("1"), d("0"), 0, {})),
        ("a negative tick", ("v", "i", "USDT", d("1"), d("-0.01"), 0, {})),
        ("a negative server time", ("v", "i", "USDT", d("1"), None, -1, {})),
        ("bars keyed by another time", ("v", "i", "USDT", d("1"), None, 0, {2 * M: bar})),
    ):
        c.refuses(label, lambda a=args: mod.Oracle(*a))
    mod.Oracle("v", "i", "USDT", d("1"), None, 0, {M: bar})
    mod.Oracle("v", "i", "USDT", d("0.001"), d("0.0001"), 0, {})
    primary, second = simple_pair(mod).primary, simple_pair(mod).second
    other = mod.Oracle("k", "XBTUSD", "USD", d("1"), None, 0, {})
    c.refuses("an empty pair id", lambda: mod.OraclePair("", primary, second))
    c.refuses("a pair across quote classes", lambda: mod.OraclePair("A", primary, other), "the two oracles of a pair share a quote class")
    mod.OraclePair("B", primary, second)
    for label, args in (
        ("a zero target", ("0", "1")),
        ("a negative target", ("-1", "1")),
        ("a zero stop", ("1", "0")),
        ("a negative stop", ("1", "-1")),
    ):
        c.refuses(label, lambda a=args: mod.Barrier("f", ORIGIN, H, "USDT", mod.Side.LONG, d(a[0]), d(a[1])))
    mod.Barrier("f", ORIGIN, H, "USDT", mod.Side.SHORT, d("0.0001"), d("0.0001"))
    return c.bad


def check_identity_and_idempotence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    forecast = mod.Directional("f-1", ORIGIN, H, "USDT")
    pair = simple_pair(mod)
    one = mod.settle_directional(forecast, pair, retries_exhausted=False)
    two = mod.settle_directional(forecast, pair, retries_exhausted=False)
    c.eq("repeating a settlement gives the same record", one, two)
    c.eq(
        "the id is the forecast, the pair and the rule version",
        one.settlement_id,
        natural_id("st", {"forecast": "f-1", "pair": "A", "rule": "settle/1"}),
    )
    other_forecast = mod.settle_directional(mod.Directional("f-2", ORIGIN, H, "USDT"), pair, retries_exhausted=False)
    other_pair = mod.settle_directional(forecast, mod.OraclePair("B", pair.primary, pair.second), retries_exhausted=False)
    c.eq("another forecast, another id", other_forecast.settlement_id != one.settlement_id, True)
    c.eq("another pair, another id", other_pair.settlement_id != one.settlement_id, True)
    c.eq(
        "the record",
        (
            one.forecast_id,
            one.contract,
            one.pair_id,
            one.rule_version,
            one.state,
            one.outcome,
            one.reason,
            one.p0,
            one.outcome_price,
            one.touch_open_ms,
            one.flags,
        ),
        ("f-1", "D", "A", "settle/1", mod.State.SETTLED, "1", None, "100.00", "101.00", None, ()),
    )
    barrier = mod.Barrier("f-1", ORIGIN, H, "USDT", mod.Side.LONG, d("2"), d("1"))
    bars = {ORIGIN - M: mod.Bar(ORIGIN - M, d("100"), d("100"), d("100"), d("100"))}
    for k in range(60):
        bars[ORIGIN + k * M] = mod.Bar(ORIGIN + k * M, d("100"), d("100"), d("100"), d("100"))
    window = mod.OraclePair(
        "A",
        mod.Oracle("a", "i", "USDT", d("1"), None, ORIGIN + 2 * H, bars),
        mod.Oracle("b", "i", "USDT", d("1"), None, ORIGIN + 2 * H, {ORIGIN - M: bars[ORIGIN - M]}),
    )
    b1 = mod.settle_barrier(barrier, window, retries_exhausted=False)
    b2 = mod.settle_barrier(barrier, window, retries_exhausted=False)
    c.eq("a barrier settlement repeats", b1, b2)
    c.eq("the contract is B and the id is the same function of the forecast", (b1.contract, b1.settlement_id), ("B", one.settlement_id))
    # the inputs are not changed
    before = copy.deepcopy(dict(pair.primary.bars))
    mod.settle_directional(forecast, pair, retries_exhausted=True, basis=d("0.5"))
    c.eq("the bars are not changed", dict(pair.primary.bars), before)
    # a pending settlement carries nothing it cannot know
    pending = mod.settle_directional(
        forecast,
        mod.OraclePair("A", mod.Oracle("a", "i", "USDT", d("1"), None, ORIGIN, pair.primary.bars), pair.second),
        retries_exhausted=False,
    )
    c.eq(
        "a pending settlement",
        (pending.state, pending.outcome, pending.reason, pending.p0, pending.outcome_price, pending.touch_open_ms),
        (mod.State.PENDING, None, "BAR_NOT_CLOSED", None, None, None),
    )
    # the keyword that says the retries are over cannot be passed by position
    try:
        mod.settle_directional(forecast, pair, True)  # type: ignore[misc]
        c.bad.append("retries_exhausted was accepted by position")
    except TypeError:
        pass
    # the basis flag, by size and not by sign
    for basis, flagged in (("0.003", False), ("0.0030001", True), ("-0.003", False), ("-0.0030001", True), ("0", False), ("0.5", True)):
        got = mod.settle_directional(forecast, pair, retries_exhausted=False, basis=d(basis))
        c.eq(f"basis {basis}", got.flags, ("BASIS_STRESS",) if flagged else ())
    c.eq("no basis, no flag", mod.settle_directional(forecast, pair, retries_exhausted=False, basis=None).flags, ())
    return c.bad


def check_void_and_identity(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = mod.void("f-9", "B", "A", "VOID_OUTAGE")
    c.eq(
        "a void",
        (v.state, v.outcome, v.reason, v.p0, v.outcome_price, v.touch_open_ms, v.flags, v.contract, v.pair_id, v.forecast_id),
        (mod.State.VOID, None, "VOID_OUTAGE", None, None, None, (), "B", "A", "f-9"),
    )
    c.eq("its id", v.settlement_id, natural_id("st", {"forecast": "f-9", "pair": "A", "rule": "settle/1"}))
    for contract in ("D", "B"):
        mod.void("f", contract, "A", "VOID_GAP")
    c.refuses("an unknown reason", lambda: mod.void("f", "D", "A", "VOID_BORED"))
    c.refuses("an unscored contract", lambda: mod.void("f", "P", "A", "VOID_GAP"))
    c.refuses("an empty contract", lambda: mod.void("f", "", "A", "VOID_GAP"))
    # a tie is a result of a directional forecast only: a barrier forecast has no tie (contract section 3)
    tie = mod.void("f-t", "D", "A", "VOID_TIE")
    c.eq("a directional forecast can be void as a tie", (tie.state, tie.contract, tie.reason), (mod.State.VOID, "D", "VOID_TIE"))
    c.refuses_exactly(
        "a barrier forecast in a tie", lambda: mod.void("f-t", "B", "A", "VOID_TIE"), "only a directional forecast can end in a tie"
    )
    c.refuses_exactly(
        "a tie of an unscored contract is refused for its contract",
        lambda: mod.void("f-t", "P", "A", "VOID_TIE"),
        "a void has a reason from the closed list and a contract",
    )
    pair = simple_pair(mod)
    c.refuses(
        "a forecast on another quote class",
        lambda: mod.settle_directional(mod.Directional("f", ORIGIN, H, "USD"), pair, retries_exhausted=False),
        "the forecast and its oracle pair disagree on the quote class",
    )
    c.refuses(
        "a barrier on another quote class",
        lambda: mod.settle_barrier(mod.Barrier("f", ORIGIN, H, "USD", mod.Side.LONG, d("1"), d("1")), pair, retries_exhausted=False),
        "the forecast and its oracle pair disagree on the quote class",
    )
    return c.bad


def check_long_prices(mod: types.ModuleType) -> list[str]:
    """Barrier levels are exact for every price a venue can print, whatever decimal context the caller has: a 31-digit close plus an
    offset of a millionth is a level of 31 digits, and a minute whose high is exactly that level touches it."""
    from decimal import localcontext

    c = Case(mod)
    p0, level = d("1234567890123456789012345.678901"), d("1234567890123456789012345.678902")
    bars = {ORIGIN - M: mod.Bar(ORIGIN - M, p0, p0, p0, p0), ORIGIN: mod.Bar(ORIGIN, p0, level, p0, p0)}
    for k in range(1, 60):
        bars[ORIGIN + k * M] = mod.Bar(ORIGIN + k * M, p0, p0, p0, p0)
    pair = mod.OraclePair(
        "A",
        mod.Oracle("a", "i", "USDT", d("1"), None, ORIGIN + 2 * H, bars),
        mod.Oracle("b", "i", "USDT", d("1"), None, ORIGIN + 2 * H, dict(bars)),
    )
    for side, target, stop, want in (
        (mod.Side.LONG, "0.000001", "5", "TARGET"),
        (mod.Side.SHORT, "5", "0.000001", "STOP"),
    ):
        forecast = mod.Barrier("f-long", ORIGIN, H, "USDT", side, d(target), d(stop))
        for precision in (28, 6):
            with localcontext() as ctx:
                ctx.prec = precision
                got = mod.settle_barrier(forecast, pair, retries_exhausted=True)
            c.eq(
                f"{side.value} at {precision} digits in the caller's context: the exact level is touched in the first minute",
                (got.outcome, got.touch_open_ms, got.p0),
                (want, ORIGIN, "1234567890123456789012345.678901"),
            )
    return c.bad


def check_caller_context(mod: types.ModuleType) -> list[str]:
    """The module's arithmetic is in its own decimal context: a caller's context that is narrower (6 or 9 digits), rounds another way
    (toward minus or plus infinity) or traps an inexact result gives the answers of the rules. A basis beyond 0.3 percent is flagged
    whatever its digits (contract section 7), and a multiplier that does not divide a close exactly still compares the two oracles."""
    from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Inexact, localcontext

    c = Case(mod)
    trapping = Context()
    trapping.traps[Inexact] = True
    contexts = {
        "6 digits": Context(prec=6),
        "9 digits": Context(prec=9),
        "ROUND_FLOOR": Context(rounding=ROUND_FLOOR),
        "ROUND_CEILING": Context(rounding=ROUND_CEILING),
        "a context that traps Inexact": trapping,
    }
    forecast = mod.Directional("f-ctx", ORIGIN, H, "USDT")
    pair = simple_pair(mod)
    beyond = ("0.0030000004", "-0.0030000004", "0.00300000049", "0.0030000000004", "-0.0030000000000000000000000000001")
    within = ("0.003", "-0.003", "0.0029999999999999999999999999999")

    def thirds(close_ref: str, close_out: str) -> Any:
        """A second oracle that quotes three tokens per unit: its closes divided by three do not end."""
        bars = {
            ORIGIN - M: mod.Bar(ORIGIN - M, d(close_ref), d(close_ref), d(close_ref), d(close_ref)),
            ORIGIN + H - M: mod.Bar(ORIGIN + H - M, d(close_out), d(close_out), d(close_out), d(close_out)),
        }
        return mod.Oracle("okx", "BTCUSDT", "USDT", d("3"), d("0.01"), ORIGIN + H + M, bars)

    tripled = mod.OraclePair("A", pair.primary, thirds("300.0001", "303.0001"))
    barrier = mod.Barrier("f-ctx", ORIGIN, H, "USDT", mod.Side.LONG, d("2"), d("1"))
    flat = {ORIGIN - M: mod.Bar(ORIGIN - M, d("100"), d("100"), d("100"), d("100"))}
    for k in range(60):
        flat[ORIGIN + k * M] = mod.Bar(ORIGIN + k * M, d("100"), d("102") if k == 5 else d("100"), d("100"), d("100"))
    window = mod.OraclePair("A", mod.Oracle("a", "i", "USDT", d("1"), None, ORIGIN + 2 * H, flat), thirds("300.0001", "300.0001"))
    for name, ctx in contexts.items():
        with localcontext(ctx):
            try:
                flags = {b: mod.settle_directional(forecast, pair, retries_exhausted=False, basis=d(b)).flags for b in beyond + within}
                directional = mod.settle_directional(forecast, tripled, retries_exhausted=False)
                barred = mod.settle_barrier(barrier, window, retries_exhausted=False, basis=d(beyond[0]))
            except ArithmeticError as exc:
                c.bad.append(f"{name}: raised {type(exc).__name__}")
                continue
        c.eq(
            f"{name}: a basis beyond 0.3 percent is flagged, one at it or within is not",
            flags,
            {**dict.fromkeys(beyond, ("BASIS_STRESS",)), **dict.fromkeys(within, ())},
        )
        c.eq(
            f"{name}: a second oracle whose closes divided by its multiplier do not end agrees with the primary",
            (directional.state, directional.outcome, directional.p0, directional.outcome_price),
            (mod.State.SETTLED, "1", "100.00", "101.00"),
        )
        c.eq(
            f"{name}: a barrier forecast with that oracle is settled on its first touch, flagged for its basis",
            (barred.state, barred.outcome, barred.touch_open_ms, barred.flags),
            (mod.State.SETTLED, "TARGET", ORIGIN + 5 * M, ("BASIS_STRESS",)),
        )
        with localcontext(ctx):
            try:
                golden = golden_failures(mod)
            except ArithmeticError as exc:
                golden = [f"the golden cases raised {type(exc).__name__}"]
        c.bad += [f"{name}: {line}" for line in golden]
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_golden,
        check_records,
        check_identity_and_idempotence,
        check_void_and_identity,
        check_long_prices,
        check_caller_context,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
