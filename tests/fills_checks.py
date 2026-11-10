"""Checks of the fill model (``hoshitori.fills``), runnable against a mutant: worked entries and exits at every cost level, the clock rules
for every second of a minute, the pessimistic bar, gaps and missing bars, the refusals, the largest fee a cost level can charge, and results
that do not depend on the caller's decimal context."""

from __future__ import annotations

import types
from collections.abc import Callable
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Any

from hoshitori.settlement import Bar

D = Decimal
M = 60_000
T0 = 1_800_000_000_000 // M * M  # a minute boundary


def contexts() -> dict[str, Context]:
    """Callers' decimal contexts other than the default: six, nine and 28 digits, each rounding half-even, toward minus infinity and toward
    plus infinity, with and without a trap on an inexact result; and one that traps nothing."""
    out: dict[str, Context] = {}
    for prec in (6, 9, 28):
        for name, rounding in (
            ("half-even", ROUND_HALF_EVEN),
            ("toward minus infinity", ROUND_FLOOR),
            ("toward plus infinity", ROUND_CEILING),
        ):
            for inexact in (False, True):
                if prec == 28 and rounding == ROUND_HALF_EVEN and not inexact:
                    continue  # the default context itself
                traps = [InvalidOperation, DivisionByZero, Overflow, *([Inexact] if inexact else [])]
                out[f"{prec} digits rounding {name}{' trapping Inexact' if inexact else ''}"] = Context(
                    prec=prec, rounding=rounding, traps=traps
                )
    out["28 digits trapping nothing"] = Context(traps=[])
    return out


def outcome(call: Callable[[], object]) -> str:
    """What a call gives, written out exactly (a decimal's digits and exponent count), or what it raised."""
    try:
        return repr(call())
    except TimeoutError:
        raise
    except Exception as exc:
        return f"raises {type(exc).__name__}: {exc}"


def bar(minute: int, o: str, h: str | None = None, low: str | None = None, c: str | None = None, v: str | None = None) -> Bar:
    """The bar that opens ``minute`` minutes after T0; unspecified prices follow the open; the volume is unknown unless given."""
    return Bar(T0 + minute * M, D(o), D(h or o), D(low or o), D(c or o), None if v is None else D(v))


def flat(first: int, last: int, price: str = "100") -> dict[int, Bar]:
    return {b.open_ms: b for b in (bar(m, price) for m in range(first, last + 1))}


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
        except self.mod.FillError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("constants", (mod.MINUTE_MS, mod.BPS, mod.LEVELS), (60_000, D(10_000), ("BASE", "STRESS", "DOUBLE_FEES")))
    costs = mod.Costs()
    c.eq(
        "the priors",
        (costs.fee_bps, costs.slip_bps, costs.stress_slip_bps, costs.latency_ms, costs.tick),
        (D(10), D(5), D(25), 15_000, None),
    )
    c.eq(
        "statuses and kinds",
        ([s.value for s in mod.Status], [k.value for k in mod.Kind]),
        (["FILLED", "PENDING", "MISSING"], ["STOP", "TARGET", "TIME", "GAP_EXIT"]),
    )
    for cls in (mod.Costs, mod.Fill, mod.Exit):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("the levels' costs", [mod.Costs().at(x) for x in mod.LEVELS], [(D(10), D(5), False), (D(10), D(25), True), (D(20), D(5), False)])
    own = getattr(mod, "_CTX", None)
    c.eq(
        "the model's own decimal context: sixty digits, half-even", None if own is None else (own.prec, own.rounding), (60, ROUND_HALF_EVEN)
    )
    return c.bad


def check_clocks(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("never before the origin", mod.earliest_fill_ms(T0 - 100_000, T0, 15_000), T0)
    c.eq("never before the authorization plus the latency", mod.earliest_fill_ms(T0 + 10_000, T0, 15_000), T0 + 25_000)
    c.eq("exactly the origin when the two meet", mod.earliest_fill_ms(T0 - 15_000, T0, 15_000), T0)
    c.eq("a latency of zero", mod.earliest_fill_ms(T0 + 7, T0, 0), T0 + 7)
    c.eq("the first bar at or after a moment: on the minute", mod.first_bar_ms(T0), T0)
    c.eq("a millisecond into a minute: the next", mod.first_bar_ms(T0 + 1), T0 + M)
    c.eq("a millisecond before a minute: that minute", mod.first_bar_ms(T0 + M - 1), T0 + M)
    # the three clocks for every second of the minute: the fill bar opens at or after both, and is never the bar that closes at T0
    costs = mod.Costs()
    for second in range(60):
        authorized = T0 - M + second * 1000
        bars = flat(-3, 10)
        fill = mod.entry("BASE", D(1000), authorized, T0, bars, costs)
        floor = max(authorized + costs.latency_ms, T0)
        ok = fill.bar_open_ms is not None and fill.bar_open_ms >= floor and fill.bar_open_ms >= T0 and fill.bar_open_ms - floor < M
        if not ok:
            c.bad.append(f"second {second}: filled in the bar opening at {fill.bar_open_ms}, earliest {floor}")
    return c.bad


def check_entry(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    costs = mod.Costs()
    bars = {**flat(0, 5, "100"), **{T0 + M: bar(1, "100", "104", "97", "101")}}
    fill = mod.entry("BASE", D(1000), T0 - 15_000, T0, bars, costs)
    c.eq("filled in the origin bar when the order is early enough", (fill.status, fill.bar_open_ms), (mod.Status.FILLED, T0))
    c.eq("a buy pays the open plus 5 bp", fill.price, D("100.05"))
    c.eq("the quantity is the amount over the price, rounded down to eight places", fill.quantity, D("9.99500249"))
    c.eq(
        "the fee is 10 bp of what was bought, to eight places",
        fill.fee,
        (D("9.99500249") * D("100.05") * D(10) / D(10_000)).quantize(D("1E-8")),
    )
    late = mod.entry("BASE", D(1000), T0 + 10_000, T0, bars, costs)
    c.eq("an order authorized after the origin waits for latency and the next bar", (late.bar_open_ms, late.price), (T0 + M, D("100.05")))
    stress = mod.entry("STRESS", D(1000), T0 + 10_000, T0, bars, costs)
    c.eq("stress: the high of the bar plus 25 bp", stress.price, D("104") * D("1.0025"))
    double = mod.entry("DOUBLE_FEES", D(1000), T0 + 10_000, T0, bars, costs)
    c.eq(
        "double fees: the base price, twice the fee",
        (double.price, double.fee),
        (late.price, (double.quantity * double.price * D(20) / D(10_000)).quantize(D("1E-8"))),
    )  # type: ignore[operator]
    c.eq("the base and double-fee fills hold the same quantity", double.quantity, late.quantity)
    ticked = mod.entry("BASE", D(1000), T0 - 15_000, T0, bars, mod.Costs(tick=D("0.1")))
    c.eq("a buy is rounded up to the tick", ticked.price, D("100.1"))
    exact = mod.entry("BASE", D(1000), T0 - 15_000, T0, bars, mod.Costs(tick=D("0.05")))
    c.eq("a price already on the tick stays", exact.price, D("100.05"))
    # a bar that is not there
    gone = {k: v for k, v in bars.items() if k != T0}
    c.eq("no bar yet: pending", mod.entry("BASE", D(1000), T0 - 15_000, T0, gone, costs).status, mod.Status.PENDING)
    c.eq(
        "no bar after the retries: missing",
        mod.entry("BASE", D(1000), T0 - 15_000, T0, gone, costs, retries_exhausted=True).status,
        mod.Status.MISSING,
    )
    nothing = mod.entry("BASE", D(1000), T0 - 15_000, T0, gone, costs)
    c.eq("and nothing is claimed of it", (nothing.bar_open_ms, nothing.price, nothing.quantity, nothing.fee), (None, None, None, None))
    # the bar that closes at the origin is never used
    before = {T0 - M: bar(-1, "90"), T0: bar(0, "100")}
    c.eq("the bar that closes at the origin is not the fill bar", mod.entry("BASE", D(1000), T0 - 3 * M, T0, before, costs).bar_open_ms, T0)
    c.eq("the level name is kept", fill.level, "BASE")
    return c.bad


def bought(mod: types.ModuleType, bars: dict[int, Bar], level: str = "BASE") -> Any:
    return mod.entry(level, D(1000), T0 - 15_000, T0, bars, mod.Costs())


def check_exit(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    costs = mod.Costs()
    end = T0 + 10 * M
    # nothing is touched: a time exit at the open of the bar that opens at the end of the horizon
    bars = flat(0, 12)
    held = bought(mod, bars)
    done = mod.exit_order("BASE", held, D(90), D(110), end, bars, costs)
    c.eq("a time exit", (done.status, done.kind, done.signal_open_ms, done.bar_open_ms), (mod.Status.FILLED, mod.Kind.TIME, None, end))
    c.eq("it sells at the open less 5 bp", done.price, D("99.95"))
    c.eq(
        "the fee is 10 bp of the sale, the proceeds are the sale less the fee",
        (done.fee, done.proceeds),
        ((held.quantity * D("99.95") * D(10) / D(10_000)).quantize(D("1E-8")), (held.quantity * D("99.95") - done.fee).quantize(D("1E-8"))),
    )  # type: ignore[operator]
    # a bar that reaches the stop: the sale is at the next bar's open
    hit = {**bars, T0 + 3 * M: bar(3, "100", "101", "89", "92"), T0 + 4 * M: bar(4, "91")}
    s = mod.exit_order("BASE", held, D(90), D(110), end, hit, costs)
    c.eq("the stop: decided by bar 3, sold in bar 4", (s.kind, s.signal_open_ms, s.bar_open_ms), (mod.Kind.STOP, T0 + 3 * M, T0 + 4 * M))
    c.eq(
        "at the stop less 5 bp: the next open is above the stop and a stop is sold at the worse of the two", s.price, D("90") * D("0.9995")
    )
    tgt = {**bars, T0 + 2 * M: bar(2, "100", "111", "99", "110"), T0 + 3 * M: bar(3, "112")}
    t = mod.exit_order("BASE", held, D(90), D(110), end, tgt, costs)
    c.eq(
        "the target", (t.kind, t.signal_open_ms, t.bar_open_ms, t.price), (mod.Kind.TARGET, T0 + 2 * M, T0 + 3 * M, D("112") * D("0.9995"))
    )
    both = {**bars, T0 + 2 * M: bar(2, "100", "111", "89", "100")}
    b = mod.exit_order("BASE", held, D(90), D(110), end, both, costs)
    c.eq("a bar that reaches both is the stop", b.kind, mod.Kind.STOP)
    exact = {**bars, T0 + 2 * M: bar(2, "100", "110", "90", "100")}
    c.eq(
        "levels touched exactly count (both: the stop)", mod.exit_order("BASE", held, D(90), D(110), end, exact, costs).kind, mod.Kind.STOP
    )
    only_high = {**bars, T0 + 2 * M: bar(2, "100", "110", "95", "100")}
    c.eq("a high exactly at the target", mod.exit_order("BASE", held, D(90), D(110), end, only_high, costs).kind, mod.Kind.TARGET)
    only_low = {**bars, T0 + 2 * M: bar(2, "100", "105", "90", "100")}
    c.eq("a low exactly at the stop", mod.exit_order("BASE", held, D(90), D(110), end, only_low, costs).kind, mod.Kind.STOP)
    just = {**bars, T0 + 2 * M: bar(2, "100", "109.99", "90.01", "100")}
    c.eq("just inside both levels: no touch", mod.exit_order("BASE", held, D(90), D(110), end, just, costs).kind, mod.Kind.TIME)
    # the bar of the fill is never examined; a touch there is not a stop
    first = {**bars, T0: bar(0, "100", "101", "80", "100")}
    c.eq(
        "a touch in the fill bar itself is ignored",
        mod.exit_order("BASE", bought(mod, first), D(90), D(110), end, first, costs).kind,
        mod.Kind.TIME,
    )
    # a touch in the last bar before the end sells in the end bar
    late = {**bars, T0 + 9 * M: bar(9, "100", "100", "85", "95"), end: bar(10, "94")}
    ls = mod.exit_order("BASE", held, D(90), D(110), end, late, costs)
    c.eq("a touch in the last bar of the horizon is sold in the bar that opens at its end", (ls.kind, ls.bar_open_ms), (mod.Kind.STOP, end))
    # the bar that opens at the end is where the sale is made, not a bar that can trigger a stop
    at_end = {**bars, end: bar(10, "100", "100", "80", "100")}
    ae = mod.exit_order("BASE", held, D(90), D(110), end, at_end, costs)
    c.eq(
        "a touch in the bar that opens at the end does not trigger a stop; it is the sale bar",
        (ae.kind, ae.bar_open_ms, ae.price),
        (mod.Kind.TIME, end, D("99.95")),
    )
    # stress and double fees
    stress_bars = {**tgt, T0 + 3 * M: bar(3, "112", "113", "108", "112")}
    st = mod.exit_order("STRESS", bought(mod, stress_bars, "STRESS"), D(90), D(110), end, stress_bars, costs)
    c.eq("stress sells at the low of the bar less 25 bp", st.price, D("108") * D("0.9975"))
    dbl = mod.exit_order("DOUBLE_FEES", bought(mod, bars, "DOUBLE_FEES"), D(90), D(110), end, bars, costs)
    c.eq(
        "double fees: twice the fee at the base price",
        (dbl.price, dbl.fee),
        (D("99.95"), (bought(mod, bars, "DOUBLE_FEES").quantity * D("99.95") * D(20) / D(10_000)).quantize(D("1E-8"))),
    )  # type: ignore[operator]
    ticked = mod.exit_order("BASE", held, D(90), D(110), end, bars, mod.Costs(tick=D("0.1")))
    c.eq("a sale is rounded down to the tick", ticked.price, D("99.9"))
    # missing bars
    holes = {k: v for k, v in bars.items() if k != T0 + 4 * M}
    p = mod.exit_order("BASE", held, D(90), D(110), end, holes, costs)
    c.eq(
        "a bar missing on the way: pending, and nothing claimed",
        (p.status, p.kind, p.price, p.proceeds),
        (mod.Status.PENDING, None, None, None),
    )
    gone = mod.exit_order("BASE", held, D(90), D(110), end, holes, costs, retries_exhausted=True)
    c.eq(
        "once the retries are over a lost minute is a gap exit at the next bar there is",
        (gone.status, gone.kind),
        (mod.Status.FILLED, mod.Kind.GAP_EXIT),
    )
    no_end = {k: v for k, v in bars.items() if k != end}
    c.eq("no bar at the end: pending", mod.exit_order("BASE", held, D(90), D(110), end, no_end, costs).status, mod.Status.PENDING)
    no_after = {**{k: v for k, v in hit.items() if k != T0 + 4 * M}}
    c.eq(
        "a stop with no next bar to sell in: pending",
        mod.exit_order("BASE", held, D(90), D(110), end, no_after, costs).status,
        mod.Status.PENDING,
    )
    # an end that is not on a minute is taken at the next one
    mid = mod.exit_order("BASE", held, D(90), D(110), end - 1, bars, costs)
    c.eq("an end a millisecond before a minute is that minute", mid.bar_open_ms, end)
    mid2 = mod.exit_order("BASE", held, D(90), D(110), end + 1, bars, costs)
    c.eq("and a millisecond after it is the next", mid2.bar_open_ms, end + M)
    # planted future: a bar after the exit changes nothing
    future = {**hit, T0 + 9 * M: bar(9, "1", "1000000", "1", "1")}
    c.eq("a bar after the exit changes nothing", mod.exit_order("BASE", held, D(90), D(110), end, future, costs), s)
    return c.bad


def check_volume_stop_and_gaps(mod: types.ModuleType) -> list[str]:
    """What a lost or empty minute does to a fill, and the stop at its worst: no fill on a minute in which
    nothing traded, a stop sold at the worse of its price and the next open, and, once the retries are over, a sale at the next bar that is
    there, flagged as a gap exit and at the adverse side of the slippage."""
    c = Case(mod)
    costs = mod.Costs()
    end = T0 + 10 * M
    c.eq("a bar's volume is unknown unless the venue gave one", bar(0, "100").volume, None)
    c.eq("and a volume of nothing is a volume", bar(0, "100", v="0").volume, D(0))
    try:
        bar(0, "100", v="-1")
        c.bad.append("a negative volume was accepted")
    except Exception as exc:
        c.eq("a negative volume is refused", type(exc).__name__, "SettlementError")
    # an entry on a minute in which nothing traded
    quiet = {**flat(0, 12), T0: bar(0, "100", v="0")}
    c.eq(
        "a buy on a zero-volume minute is not filled yet",
        mod.entry("BASE", D(1000), T0 - 15_000, T0, quiet, costs).status,
        mod.Status.PENDING,
    )
    c.eq(
        "and is missing once the retries are over",
        mod.entry("BASE", D(1000), T0 - 15_000, T0, quiet, costs, retries_exhausted=True).status,
        mod.Status.MISSING,
    )
    traded = {**flat(0, 12), T0: bar(0, "100", v="3")}
    c.eq("a minute with volume fills", mod.entry("BASE", D(1000), T0 - 15_000, T0, traded, costs).status, mod.Status.FILLED)
    little = {**flat(0, 12), T0: bar(0, "100", v="0.5")}
    c.eq(
        "a minute in which a fraction of a unit traded is a minute in which something traded",
        mod.entry("BASE", D(1000), T0 - 15_000, T0, little, costs).status,
        mod.Status.FILLED,
    )
    c.eq(
        "and so does one whose volume is not known",
        mod.entry("BASE", D(1000), T0 - 15_000, T0, flat(0, 12), costs).status,
        mod.Status.FILLED,
    )
    bars = flat(0, 12)
    held = bought(mod, bars)
    # a stop: the sale is at the worse of the stop and the next open
    up = {**bars, T0 + 3 * M: bar(3, "100", "101", "89", "92"), T0 + 4 * M: bar(4, "95", "96", "94", "95")}
    s = mod.exit_order("BASE", held, D(90), D(110), end, up, costs)
    c.eq(
        "a stop that the next open gaps back above is sold at the stop, less the slippage",
        (s.kind, s.price),
        (mod.Kind.STOP, D("90") * D("0.9995")),
    )
    down = {**bars, T0 + 3 * M: bar(3, "100", "101", "89", "92"), T0 + 4 * M: bar(4, "85", "86", "84", "85")}
    d = mod.exit_order("BASE", held, D(90), D(110), end, down, costs)
    c.eq("one that gaps through it is sold at the open, which is worse", d.price, D("85") * D("0.9995"))
    stress = bought(mod, bars, "STRESS")
    st = mod.exit_order("STRESS", stress, D(90), D(110), end, up, costs)
    c.eq("at the stress level the worse of the stop and the next low", st.price, D("90") * D("0.9975"))
    tgt = {**bars, T0 + 2 * M: bar(2, "100", "111", "99", "110"), T0 + 3 * M: bar(3, "105", "106", "104", "105")}
    c.eq(
        "a target is still sold at the next open",
        mod.exit_order("BASE", held, D(90), D(110), end, tgt, costs).price,
        D("105") * D("0.9995"),
    )
    # a minute lost in the window of the exit
    lost = {k: v for k, v in bars.items() if k != T0 + 5 * M}
    c.eq(
        "a lost minute waits while there are retries",
        mod.exit_order("BASE", held, D(90), D(110), end, lost, costs).status,
        mod.Status.PENDING,
    )
    g = mod.exit_order("BASE", held, D(90), D(110), end, lost, costs, retries_exhausted=True)
    c.eq(
        "and once they are over the sale is at the next bar there is, flagged",
        (g.status, g.kind, g.signal_open_ms, g.bar_open_ms, g.price),
        (mod.Status.FILLED, mod.Kind.GAP_EXIT, None, T0 + 6 * M, D("100") * D("0.9995")),
    )
    c.eq("with a fee as for any sale", g.fee, (held.quantity * g.price * D(10) / D(10_000)).quantize(D("1E-8")))  # type: ignore[operator]
    # an empty minute is the same as a lost one
    empty = {**bars, T0 + 5 * M: bar(5, "100", v="0")}
    e = mod.exit_order("BASE", held, D(90), D(110), end, empty, costs, retries_exhausted=True)
    c.eq("a minute in which nothing traded is a gap too", (e.kind, e.bar_open_ms), (mod.Kind.GAP_EXIT, T0 + 6 * M))
    # the sale bar of a stop is lost
    nosale = {k: v for k, v in up.items() if k != T0 + 4 * M}
    c.eq("a stop whose sale bar is lost waits", mod.exit_order("BASE", held, D(90), D(110), end, nosale, costs).status, mod.Status.PENDING)
    n = mod.exit_order("BASE", held, D(90), D(110), end, nosale, costs, retries_exhausted=True)
    c.eq(
        "and then sells at the next bar, as a gap exit that names the stop bar",
        (n.kind, n.signal_open_ms, n.bar_open_ms, n.price),
        (mod.Kind.GAP_EXIT, T0 + 3 * M, T0 + 5 * M, D("90") * D("0.9995")),
    )
    # the sale bar of a stop is there and empty: the same as lost
    emptysale = {**up, T0 + 4 * M: bar(4, "95", "96", "94", "95", v="0")}
    c.eq(
        "a stop whose sale bar is empty waits",
        mod.exit_order("BASE", held, D(90), D(110), end, emptysale, costs).status,
        mod.Status.PENDING,
    )
    es = mod.exit_order("BASE", held, D(90), D(110), end, emptysale, costs, retries_exhausted=True)
    c.eq(
        "and then sells at the next bar that traded",
        (es.kind, es.signal_open_ms, es.bar_open_ms),
        (mod.Kind.GAP_EXIT, T0 + 3 * M, T0 + 5 * M),
    )
    # the closing bar of a time exit is lost, or empty
    noclose = {k: v for k, v in bars.items() if k != end}
    emptyclose = {**bars, end: bar(10, "100", v="0")}
    c.eq("an empty closing bar waits", mod.exit_order("BASE", held, D(90), D(110), end, emptyclose, costs).status, mod.Status.PENDING)
    ec = mod.exit_order("BASE", held, D(90), D(110), end, emptyclose, costs, retries_exhausted=True)
    c.eq("and then the sale is at the next bar that traded", (ec.kind, ec.bar_open_ms), (mod.Kind.GAP_EXIT, end + M))
    nc = mod.exit_order("BASE", held, D(90), D(110), end, noclose, costs, retries_exhausted=True)
    c.eq("a time exit whose bar is lost sells at the next bar", (nc.kind, nc.bar_open_ms), (mod.Kind.GAP_EXIT, end + M))
    # nothing later at all: still missing, never invented
    nothing = dict(flat(0, 4))
    c.eq(
        "with no later bar at all the sale is missing",
        mod.exit_order("BASE", held, D(90), D(110), end, nothing, costs, retries_exhausted=True).status,
        mod.Status.MISSING,
    )
    c.eq(
        "and before the retries are over it is pending",
        mod.exit_order("BASE", held, D(90), D(110), end, nothing, costs).status,
        mod.Status.PENDING,
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    says = "a cost level is one of ('BASE', 'STRESS', 'DOUBLE_FEES')"
    c.refuses("an unknown level", lambda: mod.Costs().at("CHEAP"), says)
    c.refuses("an unknown level in an order", lambda: mod.entry("CHEAP", D(1), T0, T0, flat(0, 3), mod.Costs()), says)
    amount = "an order is for a positive amount"
    for bad in (D(0), D(-1), D("NaN"), 5, 1.5):
        c.refuses(f"an amount of {bad!r}", lambda bad=bad: mod.entry("BASE", bad, T0, T0, flat(0, 3), mod.Costs()), amount)
    for name in ("fee_bps", "slip_bps", "stress_slip_bps"):
        for bad in (D(-1), D(10_000), D("NaN"), 5):
            c.refuses(
                f"{name} of {bad!r}", lambda name=name, bad=bad: mod.Costs(**{name: bad}), f"{name} is zero or more and below ten thousand"
            )
    c.eq("zero costs are allowed", mod.Costs(fee_bps=D(0), slip_bps=D(0)).fee_bps, D(0))
    c.eq(
        "9999.99 basis points is the largest slippage",
        (mod.Costs(slip_bps=D("9999.99")).slip_bps, mod.Costs(stress_slip_bps=D("9999.99")).stress_slip_bps),
        (D("9999.99"), D("9999.99")),
    )
    latency = "the latency is a whole number of milliseconds, zero or more"
    for bad in (-1, True, 1.5, "1"):
        c.refuses(f"latency {bad!r}", lambda bad=bad: mod.Costs(latency_ms=bad), latency)
    c.eq("a latency of zero", mod.Costs(latency_ms=0).latency_ms, 0)
    for bad in (D(0), D(-1), D("NaN"), 0.1):
        c.refuses(f"tick {bad!r}", lambda bad=bad: mod.Costs(tick=bad), "the tick is a positive price step")
    held = mod.entry("BASE", D(1000), T0 - 15_000, T0, flat(0, 5), mod.Costs())
    pending = mod.entry("BASE", D(1000), T0 - 15_000, T0, {}, mod.Costs())
    c.refuses(
        "selling what was not bought",
        lambda: mod.exit_order("BASE", pending, D(90), D(110), T0 + 5 * M, flat(0, 6), mod.Costs()),
        "only a filled purchase can be sold",
    )
    c.refuses(
        "a stop at the target",
        lambda: mod.exit_order("BASE", held, D(110), D(110), T0 + 5 * M, flat(0, 6), mod.Costs()),
        "the stop is below the target",
    )
    c.refuses(
        "a stop above the target",
        lambda: mod.exit_order("BASE", held, D(111), D(110), T0 + 5 * M, flat(0, 6), mod.Costs()),
        "the stop is below the target",
    )
    return c.bad


def check_fee_limit(mod: types.ModuleType) -> list[str]:
    """Contract section 4.1: cash "must stay at or above zero". A sale brings what it sells less its fee, so no cost level may charge a fee
    above the amount it is charged on; the largest level doubles the fee, so ``fee_bps`` is at most half of ten thousand."""
    c = Case(mod)
    says = "fee_bps is at most five thousand, so that even doubled a fee is at most the amount it is charged on"
    for bad in (D("5000.00000001"), D(6000), D("9999.99")):
        c.refuses(f"fee_bps of {bad}", lambda bad=bad: mod.Costs(fee_bps=bad), says)
    top = mod.Costs(fee_bps=D(5000))
    c.eq(
        "5000 basis points is the largest: doubled, the fee is the whole amount",
        [top.at(x)[0] for x in mod.LEVELS],
        [D(5000), D(5000), D(10_000)],
    )
    bars = {**flat(-1, 4, "100"), **flat(5, 12, "500")}
    for level in mod.LEVELS:
        held = mod.entry(level, D(1000), T0 - 15_000, T0, bars, top)
        sale = mod.exit_order(level, held, D(1), D(10_000), T0 + 10 * M, bars, top)
        c.eq(f"at the largest fee a sale at the {level} level brings zero or more", (sale.kind, sale.proceeds >= 0), (mod.Kind.TIME, True))
    double = mod.exit_order(
        "DOUBLE_FEES", mod.entry("DOUBLE_FEES", D(1000), T0 - 15_000, T0, bars, top), D(1), D(10_000), T0 + 10 * M, bars, top
    )
    c.eq(
        "at double fees of 5000 basis points the sale is all fee, but not more: proceeds of zero",
        (double.price, double.fee, double.proceeds),
        (D("499.75"), D("4995.00249438"), D(0)),
    )
    return c.bad


def check_contexts(mod: types.ModuleType) -> list[str]:
    """The same order, bars and costs give the same fill and sale whatever decimal context the caller has (``SIMULATOR_SPEC`` section 1:
    every result can be recomputed from stored inputs): six or nine digits, rounding toward either infinity, a trap on an inexact result, or
    no trap at all, each against the default context."""
    c = Case(mod)
    costs = mod.Costs()
    bars = {
        **flat(-1, 12, "100"),
        T0 + M: bar(1, "100", "104", "97", "101"),
        T0 + 3 * M: bar(3, "100", "101", "89", "92"),
        T0 + 4 * M: bar(4, "91", "93", "90", "92"),
    }
    odd = flat(-1, 12, "12345.674")
    ticked = mod.Costs(slip_bps=D(0), tick=D("0.01"))

    def stop_exit(level: str) -> Any:
        return mod.exit_order(level, mod.entry(level, D(1000), T0 - 15_000, T0, bars, costs), D(90), D(110), T0 + 10 * M, bars, costs)

    operations: dict[str, Callable[[], object]] = {
        "a purchase of 1000 at an open of 100, at each level": lambda: [
            mod.entry(level, D(1000), T0 - 15_000, T0, bars, costs) for level in mod.LEVELS
        ],
        "a purchase of 20000": lambda: mod.entry("BASE", D(20000), T0 - 15_000, T0, bars, costs),
        "a purchase of 2000 at an open of 0.0000123": lambda: mod.entry("BASE", D(2000), T0 - 15_000, T0, flat(-1, 12, "0.0000123"), costs),
        "a purchase rounded up to a tick of 0.01": lambda: mod.entry("BASE", D(1000), T0 - 15_000, T0, odd, ticked),
        "the sale at the stop, at each level": lambda: [stop_exit(level) for level in mod.LEVELS],
        "the time exit after 1000 at 100": lambda: mod.exit_order(
            "BASE", bought(mod, flat(-1, 12)), D(90), D(110), T0 + 10 * M, flat(-1, 12), costs
        ),
        "a sale rounded down to a tick of 0.01": lambda: mod.exit_order(
            "BASE", mod.entry("BASE", D(1000), T0 - 15_000, T0, odd, ticked), D(1), D(10**6), T0 + 10 * M, odd, ticked
        ),
        "double fees of a fee of many digits": lambda: mod.Costs(fee_bps=D("4999.9999995")).at("DOUBLE_FEES"),
        "a fee a ten-millionth of a basis point above the largest": lambda: mod.Costs(fee_bps=D("5000.0000001")),
    }

    def results() -> dict[str, str]:
        return {name: outcome(call) for name, call in operations.items()}

    def numbers(result: Any) -> tuple[Decimal, Decimal, Decimal]:
        """A fill's price, quantity and fee, or a sale's price, fee and proceeds."""
        return (result.price, result.quantity, result.fee) if isinstance(result, mod.Fill) else (result.price, result.fee, result.proceeds)

    anchors: dict[str, object] = {
        "a purchase of 20000": (D("100.0500"), D("199.90004997"), D("20.00000000")),
        "a purchase of 2000 at an open of 0.0000123": (D("0.00001230615"), D("162520365.83334349"), D("2.00000000")),
        "a purchase rounded up to a tick of 0.01": (D("12345.68"), D("0.08099999"), D("0.99999996")),
        "the sale at the stop, at each level": [
            (D("89.9550"), D("0.89910045"), D("898.20134854")),
            (D("89.7750"), D("0.89551122"), D("894.61571035")),
            (D("89.9550"), D("1.79820090"), D("897.30224809")),
        ],
        "the time exit after 1000 at 100": (D("99.9500"), D("0.99900050"), D("998.00149838")),
        "a sale rounded down to a tick of 0.01": (D("12345.67"), D("0.99999915"), D("998.99914739")),
    }
    with localcontext(Context()):
        want = results()
        for name, value in anchors.items():
            made = operations[name]()
            c.eq(f"{name}, in the default context", [numbers(x) for x in made] if isinstance(made, list) else numbers(made), value)
    for label, ctx in contexts().items():
        with localcontext(ctx):
            got = results()
        for name, value in want.items():
            c.eq(f"{name}, in a caller's context of {label}", got[name], value)
    return c.bad


def check_exit_at(mod: types.ModuleType) -> list[str]:
    """A sale that a position's own rule asked for: the first minute at or after the moment, no stop and no target."""
    c = Case(mod)
    costs = mod.Costs()
    bars = flat(0, 12)
    held = bought(mod, bars)
    end = T0 + 10 * M
    done = mod.exit_at("BASE", held, end, bars, costs)
    c.eq(
        "a sale at the moment",
        (done.status, done.kind, done.signal_open_ms, done.bar_open_ms),
        (mod.Status.FILLED, mod.Kind.TIME, None, end),
    )
    c.eq("it sells at the open less 5 bp", done.price, D("99.95"))
    c.eq("the fee is 10 bp of the sale", done.fee, (held.quantity * D("99.95") * D(10) / D(10_000)).quantize(D("1E-8")))  # type: ignore[operator]
    c.eq("the proceeds are the sale less the fee", done.proceeds, (held.quantity * D("99.95") - done.fee).quantize(D("1E-8")))  # type: ignore[operator]
    mid = mod.exit_at("BASE", held, end + 1, bars, costs)
    c.eq("a moment inside a minute sells at the next minute that opens", mid.bar_open_ms, end + M)
    # a price that would have been a stop for exit_order is not one here: nothing is watched on the way
    dipped = {**bars, T0 + 3 * M: bar(3, "100", "101", "50", "92")}
    c.eq("no stop is watched", mod.exit_at("BASE", held, end, dipped, costs).kind, mod.Kind.TIME)
    stress = mod.exit_at("STRESS", held, end, {**bars, end: bar(10, "100", "101", "98", "100")}, costs)
    c.eq("the stress level sells at the bar's low less 25 bp", stress.price, D("98") * D("0.9975"))
    # the minute is not there: pending while the retries last, then the next minute there is, flagged
    gone = {k: v for k, v in bars.items() if k != end}
    wait = mod.exit_at("BASE", held, end, gone, costs)
    c.eq("a minute that is not there yet is pending", (wait.status, wait.price), (mod.Status.PENDING, None))
    last = mod.exit_at("BASE", held, end, gone, costs, retries_exhausted=True)
    c.eq(
        "once the retries are over, the next minute there is",
        (last.status, last.kind, last.bar_open_ms),
        (mod.Status.FILLED, mod.Kind.GAP_EXIT, end + M),
    )
    nothing = mod.exit_at("BASE", held, end, {k: v for k, v in bars.items() if k <= T0 + 5 * M}, costs, retries_exhausted=True)
    c.eq("with no later minute at all it is missing", (nothing.status, nothing.price), (mod.Status.MISSING, None))
    quiet = {**bars, end: bar(10, "100", v="0")}
    c.eq("a minute in which nothing traded is not a fill", mod.exit_at("BASE", held, end, quiet, costs).status, mod.Status.PENDING)
    c.refuses("a sale at or before the purchase", lambda: mod.exit_at("BASE", held, T0, bars, costs), "a sale comes after the purchase")
    pending = mod.entry("BASE", D(1000), T0 - 15_000, T0, {}, costs)
    c.refuses("an unfilled purchase", lambda: mod.exit_at("BASE", pending, end, bars, costs), "only a filled purchase can be sold")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_clocks,
        check_entry,
        check_exit,
        check_exit_at,
        check_volume_stop_and_gaps,
        check_refusals,
        check_fee_limit,
        check_contexts,
    ):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
