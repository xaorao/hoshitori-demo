"""Checks of scoring (``hoshitori.scoring``), runnable against a mutant: known values, the rules at their edges, and the bounds by brute force."""

from __future__ import annotations

import itertools
import math
import random
import types
from collections.abc import Callable
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, ROUND_UP, Context, Decimal, Inexact, getcontext, localcontext
from fractions import Fraction
from typing import Any

D = Decimal
DAY = 86_400_000
EPS = D("1e-45")


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def near(self, label: str, got: Decimal | None, want: Decimal | Fraction, tolerance: Decimal = EPS) -> None:
        if got is None:
            self.bad.append(f"{label}: got None")
            return
        with localcontext() as ctx:
            ctx.prec = 80
            expected = want if isinstance(want, Decimal) else D(want.numerator) / D(want.denominator)
        if abs(got - expected) > tolerance:
            self.bad.append(f"{label}: got {got}, expected {expected}")

    def refuses(self, label: str, call: Callable[[], object], says: str | None = None) -> None:
        try:
            call()
        except self.mod.ScoringError as exc:
            if says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def ratio(top: int, bottom: int) -> Decimal:
    """An exact ratio at the module's own precision, so equality with the module's 60-digit answer is meaningful."""
    with localcontext() as ctx:
        ctx.prec = 60
        return D(top) / D(bottom)


def mean60(values: list[Decimal]) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        return sum(values, D(0)) / len(values)


def neg_log(q: Fraction, terms: int) -> Decimal:
    """``-ln(q)`` for q near one by the series sum of (1 - q)^k / k, in exact fractions: independent of the decimal logarithm."""
    x = 1 - q
    total = sum((x**k / k for k in range(1, terms + 1)), Fraction(0))
    return D(total.numerator) / D(total.denominator)


def row(
    mod: types.ModuleType,
    ident: str,
    p: str,
    outcome: int,
    climate: str | None = None,
    arm: str = "L1",
    asset: str = "BTC",
    regime: str = "LOW",
) -> Any:
    return mod.Directional(ident, arm, asset, regime, D(p), outcome, None if climate is None else D(climate))


def out(
    mod: types.ModuleType,
    ident: str,
    p: str,
    reason: str = "VOID_TIE",
    climate: str | None = None,
    arm: str = "L1",
    asset: str = "BTC",
    regime: str = "LOW",
) -> Any:
    return mod.ExcludedDirectional(ident, arm, asset, regime, reason, D(p), None if climate is None else D(climate))


def check_constants_and_rounding(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "constants",
        (mod.PRECISION, mod.PROB_FLOOR, mod.PROB_CEIL, mod.HALF, mod.REPORT_PLACES, mod.DAY_MS, mod.CLIMATE_DAYS, mod.CLIMATE_MIN),
        (60, D("0.01"), D("0.99"), D("0.5"), 12, DAY, 30, 20),
    )
    c.eq("barrier classes", mod.BARRIER_CLASSES, ("TARGET", "STOP", "TIMEOUT"))
    c.eq("shadow states", mod.SHADOW_STATES, ("SETTLED", "NO_REFERENCE", "UNSETTLEABLE", "PENDING"))
    for text, want in (
        ("0.0000000000005", "0.000000000000"),
        ("0.0000000000015", "0.000000000002"),
        ("0.0000000000025", "0.000000000002"),
        ("0.0000000000035", "0.000000000004"),
        ("-0.0000000000015", "-0.000000000002"),
        ("0.5", "0.500000000000"),
        ("12345678901234567890.1", "12345678901234567890.100000000000"),
        ("1E-15", "0.000000000000"),
        ("1E+3", "1000.000000000000"),
        ("0", "0.000000000000"),
    ):
        c.eq(f"shown {text}", mod.shown(D(text)), want)
    return c.bad


def check_caller_context_is_ignored(mod: types.ModuleType) -> list[str]:
    """The caller's decimal context (a short precision, a different rounding) must not change a score."""
    c = Case(mod)
    rows = [row(mod, "a", "0.7", 1, "0.55"), row(mod, "b", "0.35", 0, "0.45"), row(mod, "c", "0.9", 0)]
    settled, gone = rows, [out(mod, "x", "0.6", climate="0.5")]
    want = (mod.summarize_directional(rows), mod.directional_sensitivity(settled, gone), mod.log_loss(D("0.3"), 1), mod.coverage(1, 2, 4))
    for precision, rounding in ((5, ROUND_DOWN), (3, ROUND_UP), (28, ROUND_HALF_UP)):
        with localcontext() as ctx:
            ctx.prec = precision
            ctx.rounding = rounding
            got = (
                mod.summarize_directional(rows),
                mod.directional_sensitivity(settled, gone),
                mod.log_loss(D("0.3"), 1),
                mod.coverage(1, 2, 4),
            )
        c.eq(f"precision {precision} and {rounding}", got, want)
    c.eq("the caller's context is left as it was", getcontext().prec, 28)
    return c.bad


def check_scores(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("clip inside", mod.clip(D("0.37")), D("0.37"))
    c.eq("clip at the floor", (mod.clip(D("0.01")), mod.clip(D("0.0099999")), mod.clip(D("0"))), (D("0.01"), D("0.01"), D("0.01")))
    c.eq("clip at the ceiling", (mod.clip(D("0.99")), mod.clip(D("0.9900001")), mod.clip(D("1"))), (D("0.99"), D("0.99"), D("0.99")))
    for label, value in (
        ("a float", 0.5),
        ("a string", "0.5"),
        ("minus", D("-0.0001")),
        ("above one", D("1.0000001")),
        ("nan", D("NaN")),
        ("infinity", D("Infinity")),
        ("none", None),
    ):
        c.refuses(f"clip refuses {label}", lambda v=value: mod.clip(v))
    c.eq(
        "brier",
        [
            mod.brier(D(p), o)
            for p, o in (("0.7", 1), ("0.7", 0), ("0.5", 1), ("0.5", 0), ("0", 1), ("1", 0), ("1", 1), ("0", 0), ("0.25", 0))
        ],
        [D("0.09"), D("0.49"), D("0.25"), D("0.25"), D("0.9801"), D("0.9801"), D("0.0001"), D("0.0001"), D("0.0625")],
    )
    with localcontext() as ctx:
        ctx.prec = 90
        half = neg_log(Fraction(1, 2), 400)  # 0.5^400 / 400 is far below 1e-90
        ninety_nine = neg_log(Fraction(99, 100), 60)  # 0.01^60 is far below 1e-90
    c.near("log loss at one half", mod.log_loss(D("0.5"), 1), half, D("1e-55"))
    c.near("log loss at one half, other way", mod.log_loss(D("0.5"), 0), half, D("1e-55"))
    c.near("log loss of 0.99", mod.log_loss(D("0.99"), 1), ninety_nine, D("1e-55"))
    c.near("log loss of a clipped one", mod.log_loss(D("1"), 1), ninety_nine, D("1e-55"))
    c.near("log loss of a clipped zero", mod.log_loss(D("0"), 0), ninety_nine, D("1e-55"))
    c.near("log loss of a clipped miss", mod.log_loss(D("0"), 1), D(repr(-math.log(0.01))), D("1e-12"))
    c.eq("one half to twelve places", mod.shown(mod.log_loss(D("0.5"), 1)), "0.693147180560")
    for p in ("0.01", "0.2", "0.37", "0.63", "0.9", "0.99"):
        for o in (0, 1):
            expected = -math.log(float(p) if o else 1 - float(p))
            c.near(f"log loss against floats {p} {o}", mod.log_loss(D(p), o), D(repr(expected)), D("1e-12"))
    for label, args in (
        ("an outcome of two", (D("0.5"), 2)),
        ("a true", (D("0.5"), True)),
        ("a false", (D("0.5"), False)),
        ("a float outcome", (D("0.5"), 1.0)),
        ("a string outcome", (D("0.5"), "1")),
    ):
        c.refuses(f"brier refuses {label}", lambda a=args: mod.brier(*a))
        c.refuses(f"log loss refuses {label}", lambda a=args: mod.log_loss(*a))
    # both scores are proper: the expected score of a forecaster who believes p is lowest when it reports exactly p
    grid = [D(k) / 100 for k in range(1, 100)]
    for believed in (D("0.05"), D("0.25"), D("0.5"), D("0.8"), D("0.95")):
        for name, kind in (("brier", mod.brier), ("log loss", mod.log_loss)):
            expected = {q: believed * kind(q, 1) + (1 - believed) * kind(q, 0) for q in grid}
            c.eq(f"{name} is minimised by the truth at {believed}", min(expected, key=lambda q: expected[q]), believed)
    third = (D("0.5"), D("0.3"), D("0.2"))
    c.eq("three-class brier", [mod.brier3(third, name) for name in ("TARGET", "STOP", "TIMEOUT")], [D("0.38"), D("0.78"), D("0.98")])
    c.eq("a certain forecast", (mod.brier3((D(1), D(0), D(0)), "TARGET"), mod.brier3((D(1), D(0), D(0)), "STOP")), (D(0), D(2)))
    c.eq("probabilities may be given as a list", mod.brier3([D("0.5"), D("0.3"), D("0.2")], "TARGET"), D("0.38"))
    c.refuses("two probabilities", lambda: mod.brier3((D("0.5"), D("0.5")), "TARGET"))
    c.refuses("four probabilities", lambda: mod.brier3((D("0.25"),) * 4, "TARGET"))
    c.refuses("a sum above one", lambda: mod.brier3((D("0.5"), D("0.3"), D("0.3")), "TARGET"))
    c.refuses("a sum below one", lambda: mod.brier3((D("0.5"), D("0.3"), D("0.1999999")), "TARGET"))
    c.refuses("a negative part", lambda: mod.brier3((D("1.1"), D("-0.1"), D("0")), "TARGET"))
    c.refuses("a float part", lambda: mod.brier3((0.5, D("0.3"), D("0.2")), "TARGET"))
    c.refuses("an unknown class", lambda: mod.brier3(third, "AMBIGUOUS"))
    c.refuses("a lower-case class", lambda: mod.brier3(third, "target"))
    return c.bad


def check_climatology(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    now = 100 * DAY
    inside = [(now - 5 * DAY + i, 1 if i < 7 else 0) for i in range(20)]  # seven rises in twenty
    c.eq("a rate", mod.climatology(inside, now), D("0.35"))
    c.eq("the minimum is twenty", mod.climatology(inside[:19], now), None)
    c.eq("a smaller minimum", mod.climatology(inside[:10], now, minimum=10), D("0.7"))
    c.eq("the minimum can be raised", mod.climatology(inside, now, minimum=21), None)
    edge = [(now - 30 * DAY, 1)] * 20
    c.eq("exactly thirty days back is read", mod.climatology(edge, now), D(1))
    c.eq("a millisecond earlier is not", mod.climatology([(now - 30 * DAY - 1, 1)] * 20, now), None)
    c.eq("a millisecond before the creation is read", mod.climatology([(now - 1, 0)] * 20, now), D(0))
    c.eq("the creation millisecond is later data", mod.climatology([(now, 1)] * 20, now), None)
    c.eq("after the creation is later data", mod.climatology([(now + 1, 1)] * 20 + [(now + DAY, 1)] * 20, now), None)
    c.eq("later data does not change the rate", mod.climatology(inside + [(now, 1)] * 50 + [(now - 31 * DAY, 1)] * 50, now), D("0.35"))
    c.eq("an iterator is read once", mod.climatology(iter(inside), now), D("0.35"))
    c.eq("an early window", mod.climatology([(5, 1)] * 20, 10), D(1))
    c.refuses("an outcome of two", lambda: mod.climatology([(now - 1, 2)] * 20, now))
    c.refuses("a true outcome", lambda: mod.climatology([(now - 1, True)] * 20, now))
    c.refuses("a bad outcome outside the window is still refused", lambda: mod.climatology([(now - 40 * DAY, 2), *inside], now))
    c.refuses("a float known time", lambda: mod.climatology([(now - 1.5, 1)] * 20, now))
    c.refuses("a boolean known time", lambda: mod.climatology([(True, 1)] * 20, now))
    c.refuses("a negative creation time", lambda: mod.climatology(inside, -1))
    c.refuses("a float creation time", lambda: mod.climatology(inside, 1.5))
    c.refuses("a boolean creation time", lambda: mod.climatology(inside, True))
    c.refuses("a zero minimum", lambda: mod.climatology(inside, now, minimum=0))
    c.eq("a creation time of zero is allowed and has no history", mod.climatology([], 0, minimum=1), None)
    c.eq("a minimum of one is allowed", mod.climatology([(now - 1, 1)], now, minimum=1), D(1))
    return c.bad


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for cls in (
        mod.Directional,
        mod.ExcludedDirectional,
        mod.Barrier,
        mod.ExcludedBarrier,
        mod.Skill,
        mod.DirectionalSummary,
        mod.BarrierSummary,
        mod.ReliabilityBin,
        mod.CoverageReport,
        mod.ExclusionCount,
        mod.Bounds,
        mod.DirectionalSensitivity,
        mod.ShadowTrade,
        mod.ShadowReport,
    ):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("the default reference", (row(mod, "a", "0.5", 1).climate, out(mod, "a", "0.5").climate), (None, None))
    for field, phrase in (("forecast_id", "an id"), ("arm", "an arm"), ("asset", "an asset"), ("regime", "a regime")):
        values = {"forecast_id": "f", "arm": "L1", "asset": "BTC", "regime": "LOW"}
        values[field] = ""
        c.refuses(
            f"a settled forecast with an empty {field}",
            lambda v=dict(values): mod.Directional(v["forecast_id"], v["arm"], v["asset"], v["regime"], D("0.5"), 1),
            f"{phrase} is a non-empty string",
        )
        c.refuses(
            f"an excluded forecast with an empty {field}",
            lambda v=dict(values): mod.ExcludedDirectional(v["forecast_id"], v["arm"], v["asset"], v["regime"], "VOID_TIE", D("0.5")),
            f"{phrase} is a non-empty string",
        )
        c.refuses(
            f"a barrier with an empty {field}",
            lambda v=dict(values): mod.Barrier(v["forecast_id"], v["arm"], v["asset"], v["regime"], (D("0.5"), D("0.3"), D("0.2")), "STOP"),
            f"{phrase} is a non-empty string",
        )
        c.refuses(
            f"an excluded barrier with an empty {field}",
            lambda v=dict(values): mod.ExcludedBarrier(
                v["forecast_id"], v["arm"], v["asset"], v["regime"], "AMBIGUOUS", (D("0.5"), D("0.3"), D("0.2"))
            ),
            f"{phrase} is a non-empty string",
        )
    c.refuses("a label that is not text", lambda: mod.Directional(1, "L1", "BTC", "LOW", D("0.5"), 1))
    c.refuses("an empty reason", lambda: mod.ExcludedDirectional("f", "L1", "BTC", "LOW", "", D("0.5")), "a reason is a non-empty string")
    c.refuses(
        "an empty barrier reason",
        lambda: mod.ExcludedBarrier("f", "L1", "BTC", "LOW", "", (D("0.5"), D("0.3"), D("0.2"))),
        "a reason is a non-empty string",
    )
    c.refuses("a settled probability above one", lambda: row(mod, "f", "1.1", 1))
    c.refuses("a settled outcome of two", lambda: row(mod, "f", "0.5", 2))
    c.refuses("a settled climate above one", lambda: row(mod, "f", "0.5", 1, climate="1.2"))
    c.refuses("an excluded probability below zero", lambda: out(mod, "f", "-0.1"))
    c.refuses("an excluded climate below zero", lambda: out(mod, "f", "0.5", climate="-0.1"))
    c.refuses("a barrier that does not sum", lambda: mod.Barrier("f", "L1", "BTC", "LOW", (D("0.5"), D("0.5"), D("0.5")), "STOP"))
    c.refuses(
        "a barrier outcome that is not a class", lambda: mod.Barrier("f", "L1", "BTC", "LOW", (D("0.5"), D("0.3"), D("0.2")), "AMBIGUOUS")
    )
    c.refuses(
        "an excluded barrier that does not sum",
        lambda: mod.ExcludedBarrier("f", "L1", "BTC", "LOW", "AMBIGUOUS", (D("0.5"), D("0.5"), D("0.5"))),
    )
    return c.bad


def check_summaries(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rows = [row(mod, "a", "0.8", 1, "0.6"), row(mod, "b", "0.8", 0, "0.6"), row(mod, "c", "0.3", 0), row(mod, "d", "0.6", 1)]
    s = mod.summarize_directional(rows)
    briers = [Fraction(4, 100), Fraction(64, 100), Fraction(9, 100), Fraction(16, 100)]
    c.eq("n", s.n, 4)
    c.near("mean brier", s.brier, sum(briers) / 4)
    c.near("brier skill against one half", s.brier_vs_half.skill, 1 - (sum(briers) / 4) / Fraction(1, 4))
    c.eq("the half reference", (s.brier_vs_half.n, s.brier_vs_half.score, s.brier_vs_half.reference), (4, s.brier, D("0.25")))
    c.near("the half reference of the log", s.log_vs_half.reference, D(repr(math.log(2))), D("1e-12"))
    losses = [-math.log(0.8), -math.log(0.2), -math.log(0.7), -math.log(0.6)]
    c.near("mean log loss", s.log_loss, D(repr(sum(losses) / 4)), D("1e-12"))
    c.eq("the log score is the same on both sides", s.log_vs_half.score, s.log_loss)
    # only the two rows with a climate reference are compared with it, on both sides
    own = [Fraction(4, 100), Fraction(64, 100)]
    ref = [(Fraction(4, 10)) ** 2, (Fraction(6, 10)) ** 2]
    c.eq("climate n", (s.brier_vs_climate.n, s.log_vs_climate.n), (2, 2))
    c.near("the comparison's own score", s.brier_vs_climate.score, sum(own) / 2)
    c.near("the climate reference score", s.brier_vs_climate.reference, sum(ref) / 2)
    c.near("brier skill against climate", s.brier_vs_climate.skill, 1 - (sum(own) / 2) / (sum(ref) / 2))
    c.near(
        "log skill against climate",
        s.log_vs_climate.skill,
        1 - D(repr((-math.log(0.8) - math.log(0.2)) / 2)) / D(repr((-math.log(0.6) - math.log(0.4)) / 2)),
        D("1e-11"),
    )
    plain = mod.summarize_directional(rows[2:])
    c.eq("without any climate reference", (plain.brier_vs_climate, plain.log_vs_climate), (None, None))
    c.eq("one row", mod.summarize_directional(rows[:1]).n, 1)
    c.refuses("no rows", lambda: mod.summarize_directional([]))
    # a perfect forecaster, a coin and an opposite one
    perfect = mod.summarize_directional([row(mod, "p", "1", 1), row(mod, "q", "0", 0)])
    c.near("a clipped perfect forecaster has a tiny score", perfect.brier, D("0.0001"))
    c.near("and a skill just below one", perfect.brier_vs_half.skill, D("0.9996"))
    coin = mod.summarize_directional([row(mod, "p", "0.5", 1), row(mod, "q", "0.5", 0)])
    c.eq("a coin has no skill", coin.brier_vs_half.skill, D(0))
    wrong = mod.summarize_directional([row(mod, "p", "1", 0)])
    c.near("a confident miss has negative skill", wrong.brier_vs_half.skill, 1 - D("0.9801") / D("0.25"))
    barriers = [
        mod.Barrier("a", "L1", "BTC", "LOW", (D("0.5"), D("0.3"), D("0.2")), "TARGET"),
        mod.Barrier("b", "L1", "BTC", "LOW", (D("0.5"), D("0.3"), D("0.2")), "STOP"),
        mod.Barrier("c", "L1", "BTC", "LOW", (D("0.5"), D("0.3"), D("0.2")), "TIMEOUT"),
        mod.Barrier("d", "L1", "BTC", "LOW", (D("0.5"), D("0.3"), D("0.2")), "TIMEOUT"),
    ]
    b = mod.summarize_barrier(barriers)
    c.eq("barrier counts", (b.n, b.counts), (4, (1, 1, 2)))
    c.near("barrier mean", b.brier3, (D("0.38") + D("0.78") + 2 * D("0.98")) / 4)
    c.refuses("no barriers", lambda: mod.summarize_barrier([]))
    return c.bad


def check_reliability(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rows = [
        row(mod, f"r{i}", p, o)
        for i, (p, o) in enumerate((("0.9", 1), ("0.1", 0), ("0.5", 1), ("0.3", 0), ("0.7", 1), ("0.2", 1), ("0.6", 0)))
    ]
    bins = mod.reliability_bins(rows, 3)
    c.eq("sizes", [b.count for b in bins], [3, 2, 2])
    c.eq("first bin", (bins[0].low, bins[0].high, bins[0].mean_p, bins[0].frequency), (D("0.1"), D("0.3"), D("0.2"), ratio(1, 3)))
    c.eq("second bin", (bins[1].low, bins[1].high, bins[1].mean_p, bins[1].frequency), (D("0.5"), D("0.6"), D("0.55"), D("0.5")))
    c.eq("third bin", (bins[2].low, bins[2].high, bins[2].mean_p, bins[2].frequency), (D("0.7"), D("0.9"), D("0.8"), D(1)))
    one = mod.reliability_bins(rows, 1)
    c.eq("one bin", (one[0].count, one[0].mean_p, one[0].frequency), (7, ratio(33, 70), ratio(4, 7)))
    each = mod.reliability_bins(rows, 7)
    c.eq("a bin each", [b.count for b in each], [1] * 7)
    clipped = mod.reliability_bins([row(mod, "x", "0", 0), row(mod, "y", "1", 1)], 2)
    c.eq("clipped probabilities", (clipped[0].mean_p, clipped[1].mean_p), (D("0.01"), D("0.99")))
    ties = mod.reliability_bins([row(mod, "b", "0.4", 1), row(mod, "a", "0.4", 0), row(mod, "c", "0.4", 1)], 3)
    c.eq("ties are ordered by id", [b.frequency for b in ties], [D(0), D(1), D(1)])
    near = mod.reliability_bins([row(mod, "x", "0.001", 0), row(mod, "y", "0.002", 1), row(mod, "z", "0.5", 1)], 2)
    c.eq("clipped values sort together, so the id decides", [b.count for b in near], [2, 1])
    c.eq("and the bin shows the clipped value", (near[0].low, near[0].high), (D("0.01"), D("0.01")))
    c.refuses("no bins", lambda: mod.reliability_bins(rows, 0))
    c.refuses("more bins than forecasts", lambda: mod.reliability_bins(rows, 8))
    c.refuses("no forecasts", lambda: mod.reliability_bins([], 1))
    return c.bad


def check_coverage_and_shadow(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = mod.coverage(6, 2, 2)
    c.eq("a report", (r.calls, r.no_calls, r.failed, r.eligible, r.coverage, r.availability), (6, 2, 2, 10, D("0.75"), D("0.8")))
    c.eq("failures leave coverage alone", mod.coverage(6, 2, 0).coverage, mod.coverage(6, 2, 50).coverage)
    c.eq("and lower availability", mod.coverage(6, 2, 50).availability, ratio(8, 58))
    c.eq("nothing decided", (mod.coverage(0, 0, 3).coverage, mod.coverage(0, 0, 3).availability), (None, D(0)))
    c.eq("nothing eligible", (mod.coverage(0, 0, 0).coverage, mod.coverage(0, 0, 0).availability), (None, None))
    c.eq("all calls", (mod.coverage(4, 0, 0).coverage, mod.coverage(4, 0, 0).availability), (D(1), D(1)))
    c.eq("only abstains", mod.coverage(0, 4, 0).coverage, D(0))
    for label, args in (
        ("a negative call count", (-1, 0, 0)),
        ("a negative abstain count", (0, -1, 0)),
        ("a negative failure count", (0, 0, -1)),
        ("a float", (1.5, 0, 0)),
        ("a boolean", (True, 0, 0)),
        ("a string", ("1", 0, 0)),
    ):
        c.refuses(label, lambda a=args: mod.coverage(*a))
    table = mod.exclusion_table(
        [
            out(mod, "1", "0.5", "VOID_TIE", arm="L2"),
            out(mod, "2", "0.5", "VOID_TIE", arm="L1"),
            out(mod, "3", "0.5", "VOID_TIE", arm="L1"),
            out(mod, "4", "0.5", "UNSETTLEABLE_MISSING", arm="L1"),
            out(mod, "5", "0.5", "VOID_TIE", arm="L1", asset="ETH"),
            out(mod, "6", "0.5", "VOID_TIE", arm="L1", regime="HIGH"),
            mod.ExcludedBarrier("7", "L1", "BTC", "LOW", "AMBIGUOUS", (D("0.5"), D("0.3"), D("0.2"))),
        ]
    )
    c.eq(
        "exclusions by arm, asset, regime and reason, in order",
        [(e.arm, e.asset, e.regime, e.reason, e.count) for e in table],
        [
            ("L1", "BTC", "HIGH", "VOID_TIE", 1),
            ("L1", "BTC", "LOW", "AMBIGUOUS", 1),
            ("L1", "BTC", "LOW", "UNSETTLEABLE_MISSING", 1),
            ("L1", "BTC", "LOW", "VOID_TIE", 2),
            ("L1", "ETH", "LOW", "VOID_TIE", 1),
            ("L2", "BTC", "LOW", "VOID_TIE", 1),
        ],
    )
    c.eq("no exclusions", mod.exclusion_table([]), ())
    trades = [
        mod.ShadowTrade("a", "SETTLED", D("0.02")),
        mod.ShadowTrade("b", "SETTLED", D("-0.01")),
        mod.ShadowTrade("c", "NO_REFERENCE"),
        mod.ShadowTrade("d", "UNSETTLEABLE"),
        mod.ShadowTrade("e", "PENDING"),
        mod.ShadowTrade("f", "PENDING"),
    ]
    s = mod.shadow_report(trades)
    c.eq(
        "a shadow report",
        (s.abstains, s.settled, s.no_reference, s.unsettleable, s.pending, s.share_settled, s.mean_return, s.hypothetical),
        (6, 2, 1, 1, 2, ratio(1, 3), D("0.005"), True),
    )
    e = mod.shadow_report([])
    c.eq("no abstains", (e.abstains, e.share_settled, e.mean_return, e.hypothetical), (0, None, None, True))
    c.eq("no settled shadow trade", mod.shadow_report([mod.ShadowTrade("a", "PENDING")]).mean_return, None)
    c.refuses("an unknown state", lambda: mod.ShadowTrade("a", "DONE"))
    c.refuses("a settled trade without a return", lambda: mod.ShadowTrade("a", "SETTLED"))
    c.refuses("a pending trade with a return", lambda: mod.ShadowTrade("a", "PENDING", D("0.1")))
    c.refuses("an infinite return", lambda: mod.ShadowTrade("a", "SETTLED", D("Infinity")))
    c.refuses("an empty id", lambda: mod.ShadowTrade("", "PENDING"), "an id is a non-empty string")
    return c.bad


def _directional_universe(
    rng: random.Random, mod: types.ModuleType, settled: int, excluded: int, climate_share: float
) -> tuple[list[Any], list[Any]]:
    def prob() -> str:
        return f"{D(rng.randint(0, 100)) / 100}"

    def climate() -> str | None:
        return prob() if rng.random() < climate_share else None

    rows = [row(mod, f"s{i}", prob(), rng.randint(0, 1), climate()) for i in range(settled)]
    gone = [out(mod, f"x{i}", prob(), climate=climate()) for i in range(excluded)]
    return rows, gone


def _brute_directional(rows: list[Any], gone: list[Any], mod: types.ModuleType) -> dict[str, tuple[Fraction, Fraction]]:
    """Every way the excluded forecasts could have ended, scored exactly with fractions; the least and greatest of each figure."""

    def b(p: Decimal, o: int) -> Fraction:
        q = Fraction(min(max(p, D("0.01")), D("0.99")))
        return (q - o) ** 2

    best: dict[str, list[Fraction]] = {"brier": [], "brier_half": [], "brier_climate": []}
    for ways in itertools.product((0, 1), repeat=len(gone)):
        settled_all = [(r.p, r.outcome, r.climate) for r in rows] + [(e.p, o, e.climate) for e, o in zip(gone, ways, strict=True)]
        best["brier"].append(sum(b(p, o) for p, o, _ in settled_all) / len(settled_all))
        best["brier_half"].append(1 - (sum(b(p, o) for p, o, _ in settled_all) / sum(b(D("0.5"), o) for _, o, _ in settled_all)))
        with_ref = [(p, o, cl) for p, o, cl in settled_all if cl is not None]
        if with_ref:
            best["brier_climate"].append(
                1 - sum(b(p, o) for p, o, _ in with_ref) / sum(b(cl, o) for _, o, cl in with_ref if cl is not None)
            )
    return {key: (min(values), max(values)) for key, values in best.items() if values}


def check_bounds(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rng = random.Random(20261029)
    for trial in range(36):
        rows, gone = _directional_universe(rng, mod, rng.randint(1, 5), rng.randint(0, 4), rng.choice([0.0, 0.5, 1.0]))
        got = mod.directional_sensitivity(rows, gone)
        want = _brute_directional(rows, gone, mod)
        c.eq(f"trial {trial} counts", (got.settled, got.excluded), (len(rows), len(gone)))
        c.near(f"trial {trial} brier lower", got.brier.lower, want["brier"][0])
        c.near(f"trial {trial} brier upper", got.brier.upper, want["brier"][1])
        c.near(f"trial {trial} half lower", got.brier_vs_half.lower, want["brier_half"][0], D("1e-40"))
        c.near(f"trial {trial} half upper", got.brier_vs_half.upper, want["brier_half"][1], D("1e-40"))
        summary = mod.summarize_directional(rows)
        c.eq(f"trial {trial} headline brier", got.brier.headline, summary.brier)
        c.eq(f"trial {trial} headline half", got.brier_vs_half.headline, summary.brier_vs_half.skill)
        c.eq(f"trial {trial} headline log", got.log_loss.headline, summary.log_loss)
        c.eq(f"trial {trial} headline log skill", got.log_vs_half.headline, summary.log_vs_half.skill)
        for name in ("brier", "log_loss", "brier_vs_half", "log_vs_half"):
            figure = getattr(got, name)
            if figure.lower > figure.upper:
                c.bad.append(f"trial {trial} {name}: the lower bound {figure.lower} is above the upper {figure.upper}")
        if "brier_climate" in want and summary.brier_vs_climate is not None:
            c.near(
                f"trial {trial} climate lower",
                got.brier_vs_climate.lower if got.brier_vs_climate else None,
                want["brier_climate"][0],
                D("1e-40"),
            )
            c.near(
                f"trial {trial} climate upper",
                got.brier_vs_climate.upper if got.brier_vs_climate else None,
                want["brier_climate"][1],
                D("1e-40"),
            )
            c.eq(
                f"trial {trial} climate headline",
                got.brier_vs_climate.headline if got.brier_vs_climate else None,
                summary.brier_vs_climate.skill,
            )
        else:
            c.eq(f"trial {trial} no climate bounds", got.brier_vs_climate, None)
            c.eq(f"trial {trial} no climate log bounds", got.log_vs_climate, None)
    # the log figures against brute force on floats
    rows, gone = _directional_universe(random.Random(7), mod, 3, 3, 1.0)
    got = mod.directional_sensitivity(rows, gone)
    losses = []
    for ways in itertools.product((0, 1), repeat=3):
        total = [(r.p, r.outcome) for r in rows] + [(e.p, o) for e, o in zip(gone, ways, strict=True)]
        losses.append(sum(float(mod.log_loss(p, o)) for p, o in total) / len(total))
    c.near("log lower", got.log_loss.lower, D(repr(min(losses))), D("1e-9"))
    c.near("log upper", got.log_loss.upper, D(repr(max(losses))), D("1e-9"))
    # no exclusions: the bounds collapse onto the headline
    rows, _ = _directional_universe(random.Random(3), mod, 4, 0, 1.0)
    flat = mod.directional_sensitivity(rows, [])
    c.eq(
        "no exclusions collapse the bounds",
        (
            flat.brier.lower == flat.brier.upper == flat.brier.headline,
            flat.brier_vs_half.lower == flat.brier_vs_half.upper == flat.brier_vs_half.headline,
        ),
        (True, True),
    )
    # sign sensitivity
    c.eq("a skill that stays positive", mod.Bounds(D("0.2"), D("0.1"), D("0.3")).changes_sign, False)
    c.eq("a skill that could become negative", mod.Bounds(D("0.2"), D("-0.1"), D("0.3")).changes_sign, True)
    c.eq("a skill that stays negative", mod.Bounds(D("-0.2"), D("-0.3"), D("-0.1")).changes_sign, False)
    c.eq(
        "a bound at zero is not a change",
        (mod.Bounds(D("0.2"), D(0), D("0.3")).changes_sign, mod.Bounds(D("-0.2"), D("-0.3"), D(0)).changes_sign),
        (False, False),
    )
    c.eq("a positive headline with only negative bounds", mod.Bounds(D("0.2"), D("-0.3"), D("-0.1")).changes_sign, True)
    c.eq("a negative headline with only positive bounds", mod.Bounds(D("-0.2"), D("0.1"), D("0.3")).changes_sign, True)
    c.eq("a headline at zero", (mod.Bounds(D(0), D(0), D(0)).changes_sign, mod.Bounds(D(0), D("-0.1"), D(0)).changes_sign), (False, False))
    good = [row(mod, f"g{i}", "0.9", 1) for i in range(6)]
    c.eq(
        "one exclusion that cannot flip a clear skill",
        mod.directional_sensitivity(good, [out(mod, "x", "0.5")]).brier_vs_half.changes_sign,
        False,
    )
    c.eq(
        "many exclusions that can",
        mod.directional_sensitivity(good[:1], [out(mod, f"x{i}", "0.99") for i in range(3)]).brier_vs_half.changes_sign,
        True,
    )
    c.refuses("no settled forecast", lambda: mod.directional_sensitivity([], [out(mod, "x", "0.5")]))
    # excluded forecasts without a climate are not part of the climate figure
    mixed = mod.directional_sensitivity([row(mod, "a", "0.7", 1, "0.5")], [out(mod, "x", "0.7"), out(mod, "y", "0.7", climate="0.5")])
    only = mod.directional_sensitivity([row(mod, "a", "0.7", 1, "0.5")], [out(mod, "y", "0.7", climate="0.5")])
    c.eq(
        "an excluded forecast without a reference is dropped from that comparison",
        (mixed.brier_vs_climate, mixed.log_vs_climate),
        (only.brier_vs_climate, only.log_vs_climate),
    )
    # barrier
    rng = random.Random(11)
    for trial in range(12):

        def triple() -> tuple[Decimal, Decimal, Decimal]:
            a = rng.randint(0, 10)
            b = rng.randint(0, 10 - a)
            return D(a) / 10, D(b) / 10, D(10 - a - b) / 10

        settled = [mod.Barrier(f"s{i}", "L1", "BTC", "LOW", triple(), rng.choice(mod.BARRIER_CLASSES)) for i in range(rng.randint(1, 4))]
        gone = [
            mod.ExcludedBarrier(f"x{i}", "L1", "BTC", "LOW", rng.choice(("AMBIGUOUS", "UNSETTLEABLE_MISSING", "VOID_GAP")), triple())
            for i in range(rng.randint(0, 3))
        ]
        got = mod.barrier_sensitivity(settled, gone)
        totals = []
        # an ambiguous minute touched both levels, so it ended at one of them; for any other reason any of the three ends is possible
        possible = [("TARGET", "STOP") if e.reason == "AMBIGUOUS" else ("TARGET", "STOP", "TIMEOUT") for e in gone]
        for ways in itertools.product(*possible):
            scores = [mod.brier3(r.probabilities, r.outcome) for r in settled] + [
                mod.brier3(e.probabilities, o) for e, o in zip(gone, ways, strict=True)
            ]
            totals.append(mean60(scores))
        c.near(f"barrier trial {trial} lower", got.lower, min(totals))
        c.near(f"barrier trial {trial} upper", got.upper, max(totals))
        c.eq(f"barrier trial {trial} headline", got.headline, mod.summarize_barrier(settled).brier3)
    c.refuses("no settled barrier", lambda: mod.barrier_sensitivity([], []))
    # the review's case (A5 p08): a minute that touched both levels cannot end in TIMEOUT (contract section 3)
    first = [mod.Barrier("b1", "L0", "BTC", "LOW", (D("0.6"), D("0.3"), D("0.1")), "TARGET")]
    timeout_likely, timeout_unlikely = (D("0.1"), D("0.1"), D("0.8")), (D("0.4"), D("0.4"), D("0.2"))
    both = mod.barrier_sensitivity(first, [mod.ExcludedBarrier("b2", "L0", "BTC", "LOW", "AMBIGUOUS", timeout_likely)])
    c.eq(
        "an ambiguous exclusion is bounded over TARGET and STOP only",
        (both.headline, both.lower, both.upper),
        (D("0.26"), D("0.86"), D("0.86")),
    )
    two = mod.barrier_sensitivity(
        first,
        [
            mod.ExcludedBarrier("b2", "L0", "BTC", "LOW", "AMBIGUOUS", timeout_likely),
            mod.ExcludedBarrier("b3", "L0", "BTC", "LOW", "AMBIGUOUS", timeout_unlikely),
        ],
    )
    c.eq("for the upper bound as for the lower", (two.lower, two.upper), (D("0.76"), D("0.76")))
    lost = mod.barrier_sensitivity(first, [mod.ExcludedBarrier("b2", "L0", "BTC", "LOW", "UNSETTLEABLE_MISSING", timeout_likely)])
    c.eq(
        "an exclusion for another reason may have ended in any of the three",
        (lost.headline, lost.lower, lost.upper),
        (D("0.26"), D("0.16"), D("0.86")),
    )
    c.eq("the ends an ambiguous minute can have", getattr(mod, "AMBIGUOUS_OUTCOMES", None), ("TARGET", "STOP"))
    return c.bad


def check_caller_traps(mod: types.ModuleType) -> list[str]:
    """A caller's context that traps an inexact result, or is narrower, or rounds toward minus or plus infinity, changes no score: the
    module computes in its own context, with its own precision, rounding and traps (A5 p16: ``log_loss(0.62, 1)`` raised ``Inexact``)."""
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
    rows = [row(mod, "a", "0.7", 1, "0.55"), row(mod, "b", "0.35", 0, "0.45"), row(mod, "c", "0.9", 0)]
    gone = [out(mod, "x", "0.6", climate="0.5")]
    barriers = [
        mod.Barrier("b1", "L1", "BTC", "LOW", (D("0.6"), D("0.3"), D("0.1")), "TARGET"),
        mod.Barrier("b2", "L1", "BTC", "LOW", (D("0.5"), D("0.3"), D("0.2")), "STOP"),
    ]
    excluded = [
        mod.ExcludedBarrier("x1", "L1", "BTC", "LOW", "AMBIGUOUS", (D("0.1"), D("0.1"), D("0.8"))),
        mod.ExcludedBarrier("x2", "L1", "BTC", "LOW", "VOID_GAP", (D("0.4"), D("0.4"), D("0.2"))),
    ]
    history = [(1000 + i, 1 if i % 3 == 0 else 0) for i in range(21)]  # seven rises in twenty-one: a rate that does not end
    trades = [mod.ShadowTrade("a", "SETTLED", D("0.02")), mod.ShadowTrade("b", "SETTLED", D("-0.01")), mod.ShadowTrade("c", "PENDING")]

    def scores() -> tuple[Any, ...]:
        return (
            mod.log_loss(D("0.62"), 1),
            mod.shown(mod.log_loss(D("0.62"), 1)),
            mod.brier(D("0.62"), 1),
            mod.summarize_directional(rows),
            mod.directional_sensitivity(rows, gone),
            mod.summarize_barrier(barriers),
            mod.barrier_sensitivity(barriers, excluded),
            mod.reliability_bins(rows, 2),
            mod.climatology(history, 2000),
            mod.coverage(1, 2, 4),
            mod.shadow_report(trades),
        )

    want = scores()
    for name, ctx in contexts.items():
        with localcontext(ctx):
            try:
                got = scores()
            except ArithmeticError as exc:
                c.bad.append(f"{name}: raised {type(exc).__name__}")
                continue
        c.eq(f"{name}: the same scores", got, want)
    c.eq("the caller's context is left as it was", (getcontext().prec, getcontext().traps[Inexact]), (28, False))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants_and_rounding,
        check_caller_context_is_ignored,
        check_scores,
        check_climatology,
        check_records,
        check_summaries,
        check_reliability,
        check_coverage_and_shadow,
        check_bounds,
        check_caller_traps,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise  # the sweep's alarm: a mutant that never ends is noticed, and no later check may run without the alarm
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
