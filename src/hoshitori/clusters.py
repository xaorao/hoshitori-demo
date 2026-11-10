"""Which tokens move together, for the cluster cap of the practice book (contract section 4.1).

Tokens whose trailing 30-day daily-return correlation is at least 0.8 form a cluster, and the gross exposure of a cluster is capped
(``risk_kernel.Limits.cluster``). This module finds the clusters from daily closes; the kernel applies the cap.

* **Returns.** The simple return between two consecutive daily closes, keyed by the day it ends on. A day whose previous day is missing
  has no return; nothing is filled in.
* **Correlation, exactly.** Two tokens are joined when their returns on the days both have (at least ``MIN_RETURNS`` days) have a
  correlation of at least 0.8. The test is made on the squares, ``cov > 0 and cov**2 >= 0.64 * var_x * var_y``, in exact fractions, so
  there is no square root and no rounding at the line, and a token whose returns do not vary is joined to nothing.
* **A cluster is a connected group.** If A moves with B and B with C, the three are one cluster: the cap is on what moves together, and
  the kernel allows a token in one cluster at most.
* **What cannot be known is held tight.** A token with fewer than ``MIN_RETURNS`` returns has no known correlation. The tokens that have
  none are held together as one cluster: the cap can only lower an exposure, so not knowing never lets more through.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Final

DAY_MS: Final = 86_400_000
MIN_RETURNS: Final = 20  # days both tokens have a return on, and days a token needs before its correlation is known
WINDOW_DAYS: Final = 30  # the trailing window of the correlation (the daily closes asked for are this many plus one)
THRESHOLD_SQUARED: Final = Fraction(16, 25)  # 0.8 squared


def returns(closes: Mapping[int, Decimal]) -> dict[int, Fraction]:
    """The simple daily returns of closes keyed by the day's open (ms), by the day each return ends on; a gap in the days gives none."""
    out: dict[int, Fraction] = {}
    for day, close in closes.items():
        before = closes.get(day - DAY_MS)
        if before is not None and before > 0:
            out[day] = Fraction(close) / Fraction(before) - 1
    return out


def correlated(a: Mapping[int, Fraction], b: Mapping[int, Fraction]) -> bool:
    """Whether the returns on the days both have correlate at 0.8 or more; false with too few such days or if either is constant."""
    days = sorted(set(a) & set(b))
    if len(days) < MIN_RETURNS:
        return False
    n = len(days)
    mean_a, mean_b = sum((a[d] for d in days), Fraction(0)) / n, sum((b[d] for d in days), Fraction(0)) / n
    cov = sum(((a[d] - mean_a) * (b[d] - mean_b) for d in days), Fraction(0))
    var_a = sum(((a[d] - mean_a) ** 2 for d in days), Fraction(0))
    var_b = sum(((b[d] - mean_b) ** 2 for d in days), Fraction(0))
    return cov > 0 and var_a > 0 and var_b > 0 and cov * cov >= THRESHOLD_SQUARED * var_a * var_b


def clusters(closes_by_symbol: Mapping[str, Mapping[int, Decimal]], symbols: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """The clusters among ``symbols``: groups of two or more, each sorted, in the order of their first symbol.

    A symbol with no closes at all, or too few returns, is in the group of tokens whose correlation is not known."""
    seen = sorted(set(symbols))
    rets = {s: returns(closes_by_symbol.get(s, {})) for s in seen}
    known = [s for s in seen if len(rets[s]) >= MIN_RETURNS]
    unknown = [s for s in seen if s not in known]
    parent = {s: s for s in known}

    def root(s: str) -> str:
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    for i, x in enumerate(known):
        for y in known[i + 1 :]:
            if correlated(rets[x], rets[y]):
                parent[root(y)] = root(x)
    groups: dict[str, list[str]] = {}
    for s in known:
        groups.setdefault(root(s), []).append(s)
    found = [sorted(g) for g in groups.values() if len(g) > 1]
    if len(unknown) > 1:
        found.append(unknown)
    return tuple(tuple(g) for g in sorted(found))
