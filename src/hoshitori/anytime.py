"""Anytime-valid comparison of two forecasters: a confidence sequence for their average score difference, and evidence that one is better.

The Track record is read whenever anyone opens it, so a comparison is watched continuously. A fixed-time interval loses its error
guarantee under that watching (the chance that it ever excludes the truth grows with every look). A confidence sequence does not: with
probability at least ``1 - alpha`` it covers the running average difference at every time at once, and so at whatever time a reader stops.

Built after Choe and Ramdas, *Comparing Sequential Forecasters* (arXiv 2110.00115v6, Operations Research), Theorems 2 and 3 and appendix
B.1, and the mixture boundary of ``confseq`` (Howard et al.); both are MIT-licensed and credited in ``NOTICE.md``. Nothing is assumed
about how outcomes arise (no stationarity, no independence); the only condition is that each score difference is bounded, which the Brier
score of a probability forecast on a yes-or-no event is (a difference lies in ``[-1, 1]``).

* **The sequence** (Theorem 2, empirical Bernstein). For differences ``d_1 .. d_t`` with ``|d_i| <= c / 2``, centre ``mean(d_1 .. d_t)``,
  radius ``u(V_t) / t``, where ``V_t = max(1, sum (d_i - g_i)^2)`` with ``g_i`` the mean of the differences before ``i`` (0 for the first),
  and ``u`` the one-sided gamma-exponential mixture boundary at ``alpha / 2`` on each side.
* **The boundary** (appendix B.1 and ``confseq``). ``u(v)`` is the largest ``s`` with ``log m(s, v) < log(1 / alpha)``, where ``m`` is the
  closed form of the mixture over a gamma density whose parameter ``rho`` is set so the boundary is tightest at ``v_opt`` (``confseq``'s
  closed-form choice). It is found by doubling an upper limit from ``v`` and bisecting from 0, as ``confseq`` does.
* **The evidence** (Theorem 3). ``E_t = m(sum d_i, V_t)`` is an e-process for "the first forecaster is no better on average": under that
  null its expectation at any stopping time is at most 1, so ``E_t >= 2 / alpha`` is evidence at level ``alpha / 2``, the same moment the
  sequence leaves zero on that side.

The regularized lower incomplete gamma function is computed here (a series below ``a + 1``, a continued fraction above), so the module
has no dependency beyond the standard library.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

EPS: Final = 1e-15
MAX_TERMS: Final = 10_000
DOUBLINGS: Final = 50
BISECTIONS: Final = 200


class AnytimeError(ValueError):
    """Arguments outside what the guarantee covers."""


def gamma_p(a: float, x: float) -> float:
    """The regularized lower incomplete gamma function ``P(a, x)``, for ``a > 0`` and ``x >= 0``."""
    if not a > 0 or x < 0 or math.isnan(x):
        raise AnytimeError("P(a, x) needs a > 0 and x >= 0")
    if x == 0:
        return 0.0
    if math.isinf(x):
        return 1.0
    log_front = a * math.log(x) - x - math.lgamma(a)
    if x < a + 1:  # the series converges quickly
        term = total = 1.0 / a
        n = a
        for _ in range(MAX_TERMS):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * EPS:
                return min(1.0, math.exp(log_front) * total)
        raise AnytimeError("the series for P(a, x) did not converge")
    # the continued fraction for Q(a, x) = 1 - P(a, x), by the modified Lentz method
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, MAX_TERMS):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        step = d * c
        h *= step
        if abs(step - 1) < EPS:
            return max(0.0, 1.0 - math.exp(log_front) * h)
    raise AnytimeError("the continued fraction for Q(a, x) did not converge")


def best_rho(v_opt: float, alpha_opt: float) -> float:
    """The mixture parameter that makes a one-sided boundary at level ``alpha_opt`` tightest near ``v_opt`` (``confseq``'s closed form)."""
    if not v_opt > 0 or not 0 < alpha_opt < 0.5:
        raise AnytimeError("v_opt is positive and alpha_opt is between 0 and 0.5")
    lg = math.log(1 / (2 * alpha_opt))
    return v_opt / (2 * lg + math.log(1 + 2 * lg))


@dataclass(frozen=True)
class Mixture:
    """The gamma-exponential mixture of scale ``c`` and parameter ``rho``: ``log m(s, v)`` in closed form."""

    rho: float
    c: float

    def __post_init__(self) -> None:
        if not self.rho > 0 or not self.c > 0:
            raise AnytimeError("rho and c are positive")

    @property
    def leading(self) -> float:
        r = self.rho / (self.c * self.c)
        return r * math.log(r) - math.lgamma(r) - math.log(gamma_p(r, r))

    def log_m(self, s: float, v: float) -> float:
        """``log m(s, v)`` (appendix B.1, equation 43); where it has no closed form, the upper bound of equation 44."""
        if v < 0:
            raise AnytimeError("the intrinsic time is not negative")
        c2 = self.c * self.c
        a = (v + self.rho) / c2
        z = (self.c * s + v + self.rho) / c2
        if z <= 0:  # the sum argues for the null: m is at most this, and at most one
            return self.leading - self.rho / c2 - math.log(a)
        return self.leading + math.lgamma(a) + math.log(gamma_p(a, z)) - a * math.log(z) + (self.c * s + v) / c2

    def bound(self, v: float, alpha: float) -> float:
        """The boundary ``u(v)``: the largest ``s`` with ``log m(s, v) < log(1 / alpha)``."""
        if not 0 < alpha < 1:
            raise AnytimeError("alpha is between 0 and 1")
        threshold = math.log(1 / alpha)
        upper = max(v, 1.0)
        for _ in range(DOUBLINGS):
            if self.log_m(upper, v) > threshold:
                break
            upper *= 2
        else:
            raise AnytimeError("no upper limit for the boundary was found")
        lower = 0.0
        for _ in range(BISECTIONS):
            middle = (lower + upper) / 2
            if self.log_m(middle, v) > threshold:
                upper = middle
            else:
                lower = middle
            if upper - lower <= 1e-12 * max(1.0, upper):
                break
        return (lower + upper) / 2


@dataclass(frozen=True)
class Step:
    """The comparison after ``n`` differences: the running mean, the interval that covers it at every time, and the evidence each way."""

    n: int
    mean: float
    lower: float
    upper: float
    evidence_first: float  # e-process against "the first forecaster is no better on average"
    evidence_second: float  # against "the second is no better"


def sequence(differences: Sequence[float], *, alpha: float = 0.05, bound: float = 1.0, v_opt: float = 10.0) -> tuple[Step, ...]:
    """The confidence sequence and the two e-processes over ``differences`` (first forecaster's score less the second's, higher better).

    ``bound`` is the most any difference can be in size (1 for a Brier difference); ``v_opt`` is the intrinsic time at which the interval
    is tightest. Fixed before the data are seen: choosing either after looking would undo the guarantee."""
    if not 0 < alpha < 1:
        raise AnytimeError("alpha is between 0 and 1")
    if not bound > 0:
        raise AnytimeError("the bound is positive")
    c = 2 * bound  # Theorem 2 asks |d| <= c / 2
    side = alpha / 2
    mixture = Mixture(best_rho(v_opt, side), c)
    out: list[Step] = []
    total = 0.0
    squares = 0.0
    for n, d in enumerate(differences, start=1):
        if not -bound <= d <= bound or math.isnan(d):
            raise AnytimeError(f"difference {n} lies outside [-{bound}, {bound}]")
        centre_before = total / (n - 1) if n > 1 else 0.0
        squares += (d - centre_before) ** 2
        total += d
        v = max(1.0, squares)
        radius = mixture.bound(v, side) / n
        mean = total / n
        out.append(
            Step(n, mean, mean - radius, mean + radius, math.exp(min(mixture.log_m(total, v), 700.0)),
                 math.exp(min(mixture.log_m(-total, v), 700.0)))
        )  # fmt: skip
    return tuple(out)
