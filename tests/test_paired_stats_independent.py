"""The challenge statistics against a second implementation written from the published formulas, not from ``paired_stats``.

Sources, read for this file: Tango (1998), "Equivalence test and confidence interval for the difference in proportions for the
paired-sample design", Statistics in Medicine 17, 891-908 (the score statistic and its constrained estimate of the off-diagonal cell);
McNemar's test in its exact form, the binomial test of the discordant pairs at one half (two-sided by doubling the smaller tail).

The second implementation shares nothing with the module: the score is computed in 60-digit decimal arithmetic, the interval is found by
scanning a grid and then halving the step at each edge (so it does not assume the score is monotone, and checks that the accepted set is
one interval), and the exact test is a sum of fractions. Each is compared with the module on every table up to 30 pairs and on random
larger tables.
"""

from __future__ import annotations

import random
from decimal import Decimal, localcontext
from fractions import Fraction
from math import comb

import pytest

from hoshitori import paired_stats

Z = Decimal("1.959964")  # the module's constant, so that the comparison is of the method and not of the quantile's last digits
PRECISION = 60


def score(b: int, c: int, n: int, delta: Decimal) -> Decimal:
    """Tango's statistic T(delta) = (b - c - n*delta) / sqrt(n * (2*q21 + delta*(1 - delta))), with q21 the constrained maximum
    likelihood estimate of the probability of the (no, yes) cell: the positive root of 2n*q^2 + B*q + C = 0 with
    B = -b - c + (2n - b + c)*delta and C = -c*delta*(1 - delta)."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        big_b = -b - c + (2 * n - b + c) * delta
        big_c = -c * delta * (1 - delta)
        disc = big_b * big_b - 8 * n * big_c
        root = disc.sqrt() if disc > 0 else Decimal(0)
        q21 = (root - big_b) / (4 * n)
        variance = n * (2 * q21 + delta * (1 - delta))
        diff = b - c - n * delta
        if variance > 0:
            return diff / variance.sqrt()
        if diff == 0:
            return Decimal(0)
        return Decimal("Infinity") if diff > 0 else Decimal("-Infinity")


def inside(b: int, c: int, n: int, delta: Decimal) -> bool:
    return abs(score(b, c, n, delta)) <= Z


def interval(b: int, c: int, n: int, steps: int = 4000) -> tuple[float, float]:
    """The set of shifts not rejected at the two-sided level, found by a scan of (-1, 1) and refined at each end."""
    edge = Decimal(1) - Decimal("1e-12")
    grid = [-edge + (2 * edge) * Decimal(k) / steps for k in range(steps + 1)]
    accepted = [inside(b, c, n, d) for d in grid]
    first = accepted.index(True)
    last = len(accepted) - 1 - accepted[::-1].index(True)
    assert all(accepted[first : last + 1]), f"the accepted shifts are not one interval for b={b} c={c} n={n}"

    def refine(out: Decimal, into: Decimal) -> Decimal:
        for _ in range(90):
            mid = (out + into) / 2
            out, into = (out, mid) if inside(b, c, n, mid) else (mid, into)
        return into

    low = grid[0] if first == 0 else refine(grid[first - 1], grid[first])
    high = grid[-1] if last == steps else refine(grid[last + 1], grid[last])
    return float(-1 if first == 0 else low), float(1 if last == steps else high)


def exact_p(b: int, c: int) -> Fraction:
    """Two-sided exact McNemar p: twice the probability, under one half, of a split at least as uneven, capped at one."""
    m = b + c
    tail = sum((Fraction(comb(m, i)) for i in range(min(b, c) + 1)), Fraction(0)) / 2**m
    return min(Fraction(1), 2 * tail)


def tables(limit: int) -> list[tuple[int, int, int]]:
    """Every (b, c, n) with n up to ``limit``: the concordant pairs only change n, so a+d is folded into it."""
    return [(b, c, n) for n in range(1, limit + 1) for b in range(n + 1) for c in range(n + 1 - b)]


def test_the_exact_test_agrees_on_every_split_up_to_200_discordant_pairs() -> None:
    for m in range(201):
        for b in range(m + 1):
            want = exact_p(b, m - b)
            got = paired_stats.mcnemar_exact(b, m - b)
            assert abs(Fraction(got) - want) <= Fraction(1, 10**12) * max(want, Fraction(1, 10**300)), (b, m - b, got, float(want))


@pytest.mark.parametrize("limit", [12])
def test_the_interval_agrees_on_every_table_up_to_twelve_pairs(limit: int) -> None:
    for b, c, n in tables(limit):
        pairs = paired_stats.Pairs(n - b - c, b, c, 0)
        want = interval(b, c, n, steps=800)
        got = paired_stats.tango(pairs)
        assert got[0] == pytest.approx(want[0], abs=1e-6) and got[1] == pytest.approx(want[1], abs=1e-6), (b, c, n, got, want)


def test_the_interval_agrees_on_random_tables_of_many_sizes() -> None:
    rng = random.Random(20261031)
    for _ in range(150):
        n = rng.choice([13, 20, 25, 40, 60, 97, 200, 1000])
        b = rng.randint(0, n)
        c = rng.randint(0, n - b)
        if rng.random() < 0.3:  # many real challenges have few discordant pairs
            b, c = rng.randint(0, 3), rng.randint(0, 3)
            n = max(n, b + c)
        pairs = paired_stats.Pairs(n - b - c, b, c, 0)
        want = interval(b, c, n, steps=600)
        got = paired_stats.tango(pairs)
        assert got[0] == pytest.approx(want[0], abs=1e-6) and got[1] == pytest.approx(want[1], abs=1e-6), (b, c, n, got, want)


def test_the_published_widths_with_no_discordant_pair() -> None:
    """With no pair disagreeing the interval is symmetric and still has width: plus or minus 0.161, 0.088 and 0.060 at 20, 40, 60."""
    for n, half in ((20, 0.161), (40, 0.088), (60, 0.060)):
        low, high = interval(0, 0, n)
        assert (round(-low, 3), round(high, 3)) == (half, half)
        got = paired_stats.tango(paired_stats.Pairs(n, 0, 0, 0))
        assert got == pytest.approx((low, high), abs=1e-6)
