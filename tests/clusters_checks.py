"""Checks of the clusters (``hoshitori.clusters``), runnable against a mutant."""

from __future__ import annotations

import math
import types
from decimal import Decimal, localcontext
from fractions import Fraction

DAY = 86_400_000
D0 = 20_000 * DAY  # a day open, a multiple of a day


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def closes(rets: list[str], start: int = D0, price: str = "100") -> dict[int, Decimal]:
    """Daily closes whose simple returns are exactly ``rets`` (decimals as text), the first close at ``start``."""
    out = {start: Decimal(price)}
    with localcontext() as ctx:
        ctx.prec = 200  # every product is exact
        for i, r in enumerate(rets, start=1):
            out[start + i * DAY] = out[start + (i - 1) * DAY] * (1 + Decimal(r))
    return out


def pearson(x: list[float], y: list[float]) -> float:
    """The correlation read a second way, in floating point, from the definition."""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / math.sqrt(
        sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)
    )


def pattern(unit: list[int], scale: str, length: int = 24) -> list[Decimal]:
    return [Decimal(unit[i % len(unit)]) * Decimal(scale) for i in range(length)]


def text(values: list[Decimal]) -> list[str]:
    return [format(v, "f") for v in values]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a day", mod.DAY_MS, DAY)
    c.eq("returns needed", mod.MIN_RETURNS, 20)
    c.eq("the window", mod.WINDOW_DAYS, 30)
    c.eq("0.8 squared", mod.THRESHOLD_SQUARED, Fraction(16, 25))
    return c.bad


def check_returns(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    got = mod.returns({D0: Decimal(100), D0 + DAY: Decimal(110), D0 + 2 * DAY: Decimal(99)})
    c.eq("simple returns, keyed by the day each ends on", got, {D0 + DAY: Fraction(1, 10), D0 + 2 * DAY: Fraction(-1, 10)})
    c.eq("a gap in the days gives no return", mod.returns({D0: Decimal(100), D0 + 2 * DAY: Decimal(110)}), {})
    c.eq("a zero close gives no return after it", mod.returns({D0: Decimal(0), D0 + DAY: Decimal(110)}), {})
    c.eq("no closes", mod.returns({}), {})
    c.eq("exact", mod.returns({D0: Decimal(3), D0 + DAY: Decimal(4)}), {D0 + DAY: Fraction(1, 3)})
    c.eq("a close of one is a close", mod.returns({D0: Decimal(1), D0 + DAY: Decimal(2)}), {D0 + DAY: Fraction(1)})
    return c.bad


def check_correlated(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    x_unit, z_unit = [1, -1], [1, 1, -1, -1]
    xs = pattern(x_unit, "0.01")
    zs = pattern(z_unit, "0.01")
    base = mod.returns(closes(text(xs)))
    same = mod.returns(closes(text([2 * v for v in xs])))
    c.eq("the same moves at twice the size", mod.correlated(base, same), True)
    opposite = mod.returns(closes(text([-v for v in xs])))
    c.eq("opposite moves", mod.correlated(base, opposite), False)
    # a correlation of exactly 0.8: y = 0.8 x + 0.6 z, with z not related to x over whole periods
    exact = mod.returns(closes(text([Decimal("0.8") * a + Decimal("0.6") * b for a, b in zip(xs, zs, strict=True)])))
    c.eq("exactly 0.8 is joined (the line belongs to the cluster)", mod.correlated(base, exact), True)
    c.eq("and is joined the other way round", mod.correlated(exact, base), True)
    weaker = mod.returns(closes(text([Decimal("0.6") * a + Decimal("0.8") * b for a, b in zip(xs, zs, strict=True)])))
    c.eq("0.6 is not", mod.correlated(base, weaker), False)
    # a correlation a little under 0.8: one day of y changed, with the correlation read in floating point
    ys = [Decimal("0.8") * a + Decimal("0.6") * b for a, b in zip(xs, zs, strict=True)]
    ys[3] = ys[3] + Decimal("0.004")
    r = pearson([float(v) for v in xs], [float(v) for v in ys])
    c.eq("the changed series is under the line by a margin a float can see", r < 0.8 - 1e-4, True)
    c.eq("a correlation a little under 0.8 is not joined", mod.correlated(base, mod.returns(closes(text(ys)))), False)
    flat = mod.returns(closes(["0"] * 24))
    c.eq("a token that does not move is joined to nothing", (mod.correlated(base, flat), mod.correlated(flat, flat)), (False, False))
    short = mod.returns(closes(text(xs[:19])))  # 19 returns
    c.eq("too few days in common", mod.correlated(short, short), False)
    enough = mod.returns(closes(text(xs[:20])))  # 20 returns
    c.eq("twenty days are enough", mod.correlated(enough, enough), True)
    shifted = mod.returns(closes(text(xs), start=D0 + 2 * DAY))  # an even shift keeps the alternation in step
    c.eq("only the days both have are compared", mod.correlated(base, shifted), True)
    far = mod.returns(closes(text(xs), start=D0 + 40 * DAY))
    c.eq("no day in common", mod.correlated(base, far), False)
    return c.bad


def check_clusters(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    xs = pattern([1, -1], "0.01")
    zs = pattern([1, 1, -1, -1], "0.01")
    mix = [Decimal("0.8") * a + Decimal("0.6") * b for a, b in zip(xs, zs, strict=True)]
    data = {
        "AAA": closes(text(xs)),
        "BBB": closes(text([2 * v for v in xs])),
        "CCC": closes(text(zs)),
        "DDD": closes(text([-v for v in xs])),
        "EEE": closes(text(mix)),
    }
    c.eq(
        "A and B move together; so do E and A (0.8); C and D do not",
        mod.clusters(data, ["AAA", "BBB", "CCC", "DDD", "EEE"]),
        (("AAA", "BBB", "EEE"),),
    )
    c.eq("the order given does not matter", mod.clusters(data, ["EEE", "DDD", "CCC", "BBB", "AAA"]), (("AAA", "BBB", "EEE"),))
    c.eq("a symbol given twice is one", mod.clusters(data, ["AAA", "AAA", "BBB"]), (("AAA", "BBB"),))
    c.eq("none move together", mod.clusters(data, ["AAA", "CCC", "DDD"]), ())
    # a chain: A with B (0.8), B with C (0.96), A with C only 0.6: still one cluster, because the cap is on what moves together
    chain = {
        "AAA": closes(text(xs)),
        "BBB": closes(text(mix)),
        "CCC": closes(text([Decimal("0.6") * a + Decimal("0.8") * b for a, b in zip(xs, zs, strict=True)])),
    }
    c.eq("A and C alone are not joined", mod.correlated(mod.returns(chain["AAA"]), mod.returns(chain["CCC"])), False)
    c.eq("a chain is one cluster", mod.clusters(chain, ["AAA", "BBB", "CCC"]), (("AAA", "BBB", "CCC"),))
    # not known: held together as one cluster
    c.eq("tokens with no closes are held together", mod.clusters(data, ["AAA", "NEW1", "NEW2"]), (("NEW1", "NEW2"),))
    c.eq("one unknown token is no cluster", mod.clusters(data, ["AAA", "NEW1"]), ())
    thin = {"AAA": closes(text(xs)), "BBB": closes(text(xs[:10]))}
    c.eq("a token with too few returns is not known", mod.clusters(thin, ["AAA", "BBB"]), ())
    c.eq("two thin tokens are held together", mod.clusters({"AAA": thin["BBB"], "BBB": thin["BBB"]}, ["AAA", "BBB"]), (("AAA", "BBB"),))
    c.eq("no symbols", mod.clusters(data, []), ())
    exactly = {"AAA": closes(text(xs[:20])), "BBB": closes(text(xs[:20]))}  # 20 returns each: known, and moving together
    c.eq("twenty returns are enough to be known", mod.clusters({**exactly}, ["AAA", "BBB", "NEW1"]), (("AAA", "BBB"),))
    # the group of unknown tokens takes its place in the order of the first symbol, whatever it is
    known = {"CCC": closes(text(xs)), "DDD": closes(text([2 * v for v in xs]))}
    c.eq(
        "groups are in the order of their first symbol", mod.clusters(known, ["CCC", "DDD", "AAA", "BBB"]), (("AAA", "BBB"), ("CCC", "DDD"))
    )
    # the unknown group is sorted too, whatever order the symbols come in; twelve names make an unsorted walk of a set (whose order
    # follows the process's string hashing) all but certain to show
    many = [f"NEW{n:02d}" for n in range(12)]
    c.eq("the unknown group is sorted", mod.clusters({}, list(reversed(many))), (tuple(many),))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_returns, check_correlated, check_clusters):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad


# The changes below cannot change a result, so no check can notice them (the sweep lists them, each with its reason).
EQUIVALENT_REASONS = {
    "clusters.root|statement dropped|parent[s] = parent[parent[s]]|0": "path compression only shortens later walks to the root",
    "clusters|Add->Sub|i + 1|0": "the extra pairs are a pair seen already (the test is symmetric) or a token with itself; joining twice joins once",
    "clusters|sorted removed|sorted(g)|0": "the tokens are visited in sorted order, so each group is already sorted",
    "correlated|Gt->GtE|cov > 0|0": "a zero covariance with a varying token fails the square test, and without variation the variance guards refuse",
    "correlated|Gt->GtE|var_a > 0|0": "a token that does not vary has a zero covariance, which the covariance guard refuses",
    "correlated|Gt->GtE|var_b > 0|0": "a token that does not vary has a zero covariance, which the covariance guard refuses",
    "correlated|Sub->Add|a[d] - mean_a|0": "the deviations of the other token sum to zero, so a shifted first factor adds nothing to the exact sum",
    "correlated|Sub->Add|a[d] - mean_a|1": "the deviations of the other token sum to zero, so a shifted first factor adds nothing to the exact sum",
    "correlated|Sub->Add|b[d] - mean_b|0": "the deviations of the other token sum to zero, so a shifted second factor adds nothing to the exact sum",
    "correlated|Sub->Add|b[d] - mean_b|1": "the deviations of the other token sum to zero, so a shifted second factor adds nothing to the exact sum",
    "correlated|sorted removed|sorted(set(a) & set(b))|0": "the sums are exact fractions, so the order of the days does not matter",
}
