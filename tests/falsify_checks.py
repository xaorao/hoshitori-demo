"""Checks of the deterministic falsification tests (``hoshitori.falsify``), runnable against a mutant.

The series below are built by hand so that every statistic is known in closed form: a block of alternating plus and minus ``A`` has a median
absolute deviation of exactly ``A``, a block of zeros adds nothing, and a drift of ``d`` a minute over ``n`` minutes sums to ``n * d``. Each
boundary is tested at equality (a threshold set to the exact score passes) and one step beyond it (the next float, which does not).
"""

from __future__ import annotations

import math
import types
from decimal import Decimal
from typing import Any

A = 0.001  # the size of the alternating noise
SIGMA = 1.4826 * A
TA = 600
H = 60


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def near(self, label: str, got: float, want: float, rel: float = 1e-9) -> None:
        if not math.isclose(got, want, rel_tol=rel, abs_tol=1e-12):
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.FalsifyError as exc:
            if not str(exc).startswith(says):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def returns(ta: int = TA, h: int = H, pre: float = 0.0, post: float = 0.0, noise: float = A, offset: float = 0.0) -> list[float]:
    """Minute returns of length ``ta + h``: alternating noise where the volatility is measured, a drift of ``pre`` a minute over the 55
    minutes that end five before the announcement, and a drift of ``post`` a minute over the ``h`` minutes from it."""
    r = [0.0] * (ta + h)
    for i in range(ta - 480, ta - 120):
        r[i] = offset + (noise if i % 2 == 0 else -noise)
    for i in range(ta - 60, ta - 5):
        r[i] += pre
    for i in range(ta, ta + h):
        r[i] += post
    return r


def drift_for_z(z: float, minutes: int, sigma: float = SIGMA) -> float:
    return z * sigma / math.sqrt(minutes)


def market_series(
    ta: int = TA, h: int = H, jump_proxy: float = 0.0, jump_token: float = 0.0, beta: float = 1.2
) -> tuple[list[float], list[float]]:
    """A proxy that cycles +1, +1, -1, -1 (times 0.001), and a token that is ``beta`` times it plus independent alternating noise of 0.0005
    (orthogonal to the proxy over any whole number of four minutes), with jumps over the first ten minutes of the window."""
    n = ta + h
    p = [0.001 * (1 if i % 4 < 2 else -1) for i in range(n)]
    noise = [0.0005 * (1 if i % 2 == 0 else -1) for i in range(n)]
    for i in range(ta, ta + 10):
        p[i] += jump_proxy
    t = [beta * p[i] + noise[i] for i in range(n)]
    for i in range(ta, ta + 10):
        t[i] += jump_token
    return t, p


def drifting_market(
    ta: int = TA,
    h: int = H,
    beta_now: float = 1.2,
    beta_old: float = 5.0,
    noise_now: float = 0.0005,
    noise_old: float = 0.01,
    lookback: int = 360,
    beta_minutes: int = 448,
    big_first: int = 0,
) -> tuple[list[float], list[float]]:
    """A market series that is not stationary: inside the beta window the token loads on the proxy with ``beta_now`` and outside it with
    ``beta_old``, and inside the volatility block its noise is ``noise_now`` and outside it ``noise_old``, so a window in the wrong place is
    seen. The proxy cycles +, +, -, - and the noise alternates, which are orthogonal over any whole number of four minutes."""
    block_end = ta - 120
    p = [0.001 * (1 if i % 4 < 2 else -1) for i in range(ta + h)]
    t = []
    for i in range(ta + h):
        beta = beta_now if block_end - beta_minutes <= i < block_end else beta_old
        noise = noise_now if block_end - lookback <= i < block_end else noise_old
        if block_end - lookback <= i < block_end - lookback + big_first:
            noise = 0.001
        t.append(beta * p[i] + noise * (1 if i % 2 == 0 else -1))
    return t, p


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("labels", mod.LABELS, ("FAVOURS_ORIGINAL", "FAVOURS_ALTERNATIVE", "INCONCLUSIVE", "NOT_TESTABLE"))
    c.eq("outcomes", mod.OUTCOMES, ("ALTERNATIVE_FAVOURED", "SURVIVED", "NOT_COMPLETED"))
    c.eq("tests", mod.TESTS, ("T_ONSET", "T_MARKET", "T_SOURCE", "T_PLACEBO_TIME"))
    c.eq("time anchored", sorted(mod.TIME_ANCHORED), ["T_MARKET", "T_ONSET", "T_PLACEBO_TIME"])
    c.eq(
        "reasons",
        mod.REASONS,
        (
            "series_too_short",
            "series_misaligned",
            "non_finite_value",
            "no_volatility",
            "proxy_has_no_variation",
            "no_event_time",
            "uncalibrated",
            "no_source",
        ),
    )
    c.eq("the false-alternative limit", mod.MAX_FALSE_ALTERNATIVE, 0.10)
    c.eq("the version", mod.VERSION, "falsify/1")
    c.eq("support labels", mod.SUPPORT_LABELS, ("SUPPORTED", "CONTRADICTED", "NOT_SUPPORTED", "UNVERIFIED"))
    c.eq("banned words", mod.CAUSAL_WORDS, ("caused", "because of", "driven by", "due to"))
    o, m = mod.OnsetParams(), mod.MarketParams()
    c.eq(
        "the onset parameters",
        (o.lookback, o.sigma_gap, o.pre_start, o.pre_end, o.z_move_min, o.z_pre_min, o.z_post_min),
        (360, 120, 60, 5, 3.0, 2.5, 2.5),
    )
    c.eq("the market parameters", (m.lookback, m.sigma_gap, m.beta_minutes, m.k), (360, 120, 450, 3.0))
    return c.bad


def check_onset_scores(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    s = mod.onset_scores(returns(pre=0.0, post=0.0), TA, H, mod.OnsetParams())
    c.near("sigma is 1.4826 times the median absolute deviation", s.sigma, SIGMA)
    c.eq("no drift, no score", (s.z_total, s.z_pre, s.z_post), (0.0, 0.0, 0.0))
    pre, post = 0.00004, 0.00009
    s = mod.onset_scores(returns(pre=pre, post=post), TA, H, mod.OnsetParams())
    c.near("z over the 55 minutes before", s.z_pre, pre * 55 / (SIGMA * math.sqrt(55)))
    c.near("z over the horizon", s.z_post, post * H / (SIGMA * math.sqrt(H)))
    c.near("z over the whole window", s.z_total, (pre * 55 + post * H) / (SIGMA * math.sqrt(60 + H)))
    # the volatility is measured over the 360 minutes that end 120 before the announcement, and nowhere else
    r = returns(pre=0.0, post=0.0)
    r[TA - 481] = 7.0  # one minute before the measured block
    r[TA - 119] = -7.0  # one minute after it
    c.near("a return outside the measured block does not change sigma", mod.onset_scores(r, TA, H, mod.OnsetParams()).sigma, SIGMA)
    r[TA - 480] = 7.0  # the first minute of the block: one value in 360 moves neither the median nor the deviation
    c.near("one extreme return inside the block does not move a robust sigma", mod.onset_scores(r, TA, H, mod.OnsetParams()).sigma, SIGMA)
    # a block with a different noise level
    c.near("sigma follows the noise", mod.onset_scores(returns(noise=0.002), TA, H, mod.OnsetParams()).sigma, 1.4826 * 0.002)
    # other parameters
    p = mod.OnsetParams(lookback=100, sigma_gap=50, pre_start=30, pre_end=10)
    r2 = [0.0] * (TA + H)
    for i in range(TA - 150, TA - 50):
        r2[i] = A if i % 2 == 0 else -A
    for i in range(TA - 30, TA - 10):
        r2[i] = 0.0001
    s2 = mod.onset_scores(r2, TA, H, p)
    c.near("another lookback and gap", s2.sigma, SIGMA)
    c.near("another pre window", s2.z_pre, 0.0001 * 20 / (SIGMA * math.sqrt(20)))
    c.eq(
        "a Decimal series is read as numbers",
        mod.onset_scores([Decimal(str(x)) for x in returns(post=0.0001)], TA, H, mod.OnsetParams()).z_post > 0,
        True,
    )
    return c.bad


def check_t_onset(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    params = mod.OnsetParams()

    def label(r: list[float], p: Any = params) -> str:
        return str(mod.t_onset(r, TA, H, p).result)

    c.eq("no move: nothing to date", label(returns()), "INCONCLUSIVE")
    c.eq(
        "a move carried by the minutes after: the announcement is where it began",
        label(returns(post=drift_for_z(5, H))),
        "FAVOURS_ORIGINAL",
    )
    c.eq("a move that began before: the alternative", label(returns(pre=drift_for_z(5, 55))), "FAVOURS_ALTERNATIVE")
    c.eq("the same, falling", label(returns(pre=-drift_for_z(5, 55))), "FAVOURS_ALTERNATIVE")
    c.eq("a fall after the announcement", label(returns(post=-drift_for_z(5, H))), "FAVOURS_ORIGINAL")
    c.eq("both: the earlier one decides", label(returns(pre=drift_for_z(5, 55), post=drift_for_z(5, H))), "FAVOURS_ALTERNATIVE")
    c.eq(
        "a rise before and a larger fall after: the pre drift has the other sign than the whole, so the minutes after decide",
        label(returns(pre=drift_for_z(3, 55), post=-drift_for_z(9, H))),
        "FAVOURS_ORIGINAL",
    )
    c.eq("a move that neither part carries", label(returns(pre=drift_for_z(2.4, 55), post=drift_for_z(2.4, H))), "INCONCLUSIVE")
    # boundaries at equality (a threshold set to the exact score passes) and one float beyond (it does not)
    base = returns(post=drift_for_z(5, H))
    s = mod.onset_scores(base, TA, H, params)
    c.eq("the whole move at its limit counts", label(base, mod.OnsetParams(z_move_min=s.z_total)), "FAVOURS_ORIGINAL")
    c.eq(
        "the whole move just under its limit does not",
        label(base, mod.OnsetParams(z_move_min=math.nextafter(s.z_total, math.inf))),
        "INCONCLUSIVE",
    )
    c.eq("the minutes after at their limit count", label(base, mod.OnsetParams(z_post_min=s.z_post)), "FAVOURS_ORIGINAL")
    c.eq(
        "the minutes after just under their limit do not",
        label(base, mod.OnsetParams(z_post_min=math.nextafter(s.z_post, math.inf))),
        "INCONCLUSIVE",
    )
    early = returns(pre=drift_for_z(5, 55))
    s = mod.onset_scores(early, TA, H, params)
    c.eq("the minutes before at their limit count", label(early, mod.OnsetParams(z_pre_min=s.z_pre)), "FAVOURS_ALTERNATIVE")
    c.eq(
        "the minutes before just under their limit do not",
        label(early, mod.OnsetParams(z_pre_min=math.nextafter(s.z_pre, math.inf))),
        "INCONCLUSIVE",
    )
    result = mod.t_onset(base, TA, H, params)
    c.eq("the record", (result.test, result.version, result.reason), ("T_ONSET", "falsify/1", None))
    c.eq(
        "the parameters are written as text",
        dict(result.parameters),
        {
            "lookback": "360",
            "sigma_gap": "120",
            "pre_start": "60",
            "pre_end": "5",
            "z_move_min": "3.0",
            "z_pre_min": "2.5",
            "z_post_min": "2.5",
            "ta": "600",
            "h": "60",
        },
    )
    c.eq("the statistics are written as text", sorted(result.statistics), ["sigma", "z_post", "z_pre", "z_total"])
    c.eq("a statistic has six decimals", result.statistics["z_post"], f"{mod.onset_scores(base, TA, H, params).z_post:.6f}")
    c.eq("a repeat gives the same record", mod.t_onset(base, TA, H, params), result)
    return c.bad


def check_not_testable(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def reason(r: list[float], ta: int = TA, h: int = H) -> tuple[str, str | None]:
        out = mod.t_onset(r, ta, h)
        return out.result, out.reason

    nt = "NOT_TESTABLE"
    c.eq("a series that ends before the horizon", reason(returns()[:-1]), (nt, "series_too_short"))
    c.eq("a series that starts too late for the volatility block", reason(returns(ta=479), 479), (nt, "series_too_short"))
    c.eq("exactly enough history", reason(returns(ta=480), 480)[0] != nt, True)
    c.eq("no variation where the volatility is measured", reason([0.0] * (TA + H)), (nt, "no_volatility"))
    bad = returns()
    bad[TA + 3] = float("nan")
    c.eq("a missing value in the window", reason(bad), (nt, "non_finite_value"))
    bad = returns()
    bad[TA - 300] = float("inf")
    c.eq("an infinite value in the measured block", reason(bad), (nt, "non_finite_value"))
    bad = returns()
    bad[TA - 500] = float("nan")  # before everything that is read
    c.eq("a bad value that is never read does not matter", reason(bad)[0] != nt, True)
    c.eq("a longer series is read only as far as the horizon", reason([*returns(), float("nan")])[0] != nt, True)
    t, p = market_series()
    mp = mod.MarketParams(beta_minutes=448)

    def mreason(tok: list[float], prox: list[float], ta: int = TA) -> tuple[str, str | None]:
        out = mod.t_market(tok, prox, ta, H, mp)
        return out.result, out.reason

    c.eq("a proxy shorter than the token", mreason(t, p[:-1]), (nt, "series_misaligned"))
    c.eq("a token shorter than the proxy", mreason(t[:-1], p), (nt, "series_misaligned"))
    c.eq("a market series that ends before the horizon", mreason(t[:-1], p[:-1]), (nt, "series_too_short"))
    c.eq("not enough history for the beta window", mreason(t[:], p[:], 567), (nt, "series_too_short"))
    flat = [0.001] * len(p)
    c.eq("a proxy with no variation", mreason(t, flat), (nt, "proxy_has_no_variation"))
    nan_p = p[:]
    nan_p[TA - 200] = float("nan")
    c.eq("a missing value in the proxy", mreason(t, nan_p), (nt, "non_finite_value"))
    c.eq("a token with no volatility where it is measured", mreason([0.0] * len(p), p), (nt, "no_volatility"))
    out = mod.without_event_time()
    c.eq(
        "a registry trigger has no event time",
        [(o.test, o.result, o.reason) for o in out],
        [("T_ONSET", nt, "no_event_time"), ("T_MARKET", nt, "no_event_time")],
    )
    return c.bad


def check_market(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    mp = mod.MarketParams(beta_minutes=448)
    t, p = market_series()
    s = mod.market_scores(t, p, TA, H, mp)
    c.near("beta over the 448 minutes before the gap", s.beta, 1.2, 1e-9)
    c.near("the residual's noise is the independent noise", s.sigma_residual, 1.4826 * 0.0005)
    c.eq("a quiet window", mod.t_market(t, p, TA, H, mp).result, "INCONCLUSIVE")
    # the default window of 450 minutes estimates the same beta to a fifth of a percent
    c.near("beta over the default 450 minutes", mod.market_scores(t, p, TA, H, mod.MarketParams()).beta, 1.2, 2e-3)
    big = 0.01
    t, p = market_series(jump_token=big)
    c.eq("a token-specific jump: the original explanation", mod.t_market(t, p, TA, H, mp).result, "FAVOURS_ORIGINAL")
    t, p = market_series(jump_proxy=big)
    c.eq("a market-wide jump that the token follows: the alternative", mod.t_market(t, p, TA, H, mp).result, "FAVOURS_ALTERNATIVE")
    t, p = market_series(jump_token=-big)
    c.eq("a token-specific fall", mod.t_market(t, p, TA, H, mp).result, "FAVOURS_ORIGINAL")
    t, p = market_series(jump_proxy=-big)
    c.eq("a market-wide fall", mod.t_market(t, p, TA, H, mp).result, "FAVOURS_ALTERNATIVE")
    # boundaries at equality and one float beyond, for the residual and for the raw move
    t, p = market_series(jump_token=big)
    s = mod.market_scores(t, p, TA, H, mp)
    c.eq(
        "the residual at its limit counts",
        mod.t_market(t, p, TA, H, mod.MarketParams(beta_minutes=448, k=abs(s.z_residual))).result,
        "FAVOURS_ORIGINAL",
    )
    c.eq(
        "the residual just under its limit does not",
        mod.t_market(t, p, TA, H, mod.MarketParams(beta_minutes=448, k=math.nextafter(abs(s.z_residual), math.inf))).result,
        "INCONCLUSIVE" if abs(s.z_raw) < math.nextafter(abs(s.z_residual), math.inf) else "FAVOURS_ALTERNATIVE",
    )
    t, p = market_series(jump_proxy=big)
    s = mod.market_scores(t, p, TA, H, mp)
    c.eq("a market move: the residual is small and the raw move large", (abs(s.z_residual) < 3.0, abs(s.z_raw) >= 3.0), (True, True))
    c.eq(
        "the raw move at its limit counts",
        mod.t_market(t, p, TA, H, mod.MarketParams(beta_minutes=448, k=abs(s.z_raw))).result,
        "FAVOURS_ALTERNATIVE",
    )
    c.eq(
        "the raw move just under its limit does not",
        mod.t_market(t, p, TA, H, mod.MarketParams(beta_minutes=448, k=math.nextafter(abs(s.z_raw), math.inf))).result,
        "INCONCLUSIVE",
    )
    result = mod.t_market(t, p, TA, H, mp)
    c.eq("the record", (result.test, result.version, result.reason), ("T_MARKET", "falsify/1", None))
    c.eq("its statistics", sorted(result.statistics), ["beta", "sigma_raw", "sigma_residual", "z_raw", "z_residual"])
    c.eq(
        "its parameters",
        dict(result.parameters),
        {"lookback": "360", "sigma_gap": "120", "beta_minutes": "448", "k": "3.0", "ta": "600", "h": "60"},
    )
    c.eq("a repeat gives the same record", mod.t_market(t, p, TA, H, mp), result)
    return c.bad


def check_source_and_placebo(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for support, want in (
        ("SUPPORTED", "FAVOURS_ORIGINAL"),
        ("CONTRADICTED", "FAVOURS_ALTERNATIVE"),
        ("NOT_SUPPORTED", "FAVOURS_ALTERNATIVE"),
        ("UNVERIFIED", "INCONCLUSIVE"),
    ):
        r = mod.t_source(support)
        c.eq(f"{support}", (r.test, r.result, r.reason, dict(r.statistics)), ("T_SOURCE", want, None, {"support": support}))
    r = mod.t_source(None)
    c.eq("no source", (r.test, r.result, r.reason), ("T_SOURCE", "NOT_TESTABLE", "no_source"))
    c.refuses("an unknown support label", lambda: mod.t_source("MAYBE"), "support is one of")
    c.refuses("a lower-case label", lambda: mod.t_source("supported"), "support is one of")
    r = mod.t_placebo_time()
    c.eq(
        "the placebo test is left out until it is calibrated",
        (r.test, r.result, r.reason),
        ("T_PLACEBO_TIME", "NOT_TESTABLE", "uncalibrated"),
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = returns()
    c.refuses("a negative announcement minute", lambda: mod.t_onset(r, -1, H), "ta is a whole number of minutes")
    c.refuses("a fractional announcement minute", lambda: mod.t_onset(r, 600.5, H), "ta is a whole number of minutes")  # type: ignore[arg-type]
    c.refuses("a boolean announcement minute", lambda: mod.t_onset(r, True, H), "ta is a whole number of minutes")  # type: ignore[arg-type]
    c.refuses("a zero horizon", lambda: mod.t_onset(r, TA, 0), "h is a whole number of minutes")
    c.refuses("a negative horizon", lambda: mod.t_onset(r, TA, -5), "h is a whole number of minutes")
    c.refuses("a boolean horizon", lambda: mod.t_onset(r, TA, True), "h is a whole number of minutes")  # type: ignore[arg-type]
    c.refuses("text in a series", lambda: mod.t_onset(["0.1"] * (TA + H), TA, H), "a series is a sequence of numbers")  # type: ignore[list-item]
    c.refuses("a boolean in a series", lambda: mod.t_onset([True] * (TA + H), TA, H), "a series is a sequence of numbers")  # type: ignore[list-item]
    c.refuses("none in a series", lambda: mod.t_onset([None] * (TA + H), TA, H), "a series is a sequence of numbers")  # type: ignore[list-item]
    c.refuses("not a sequence", lambda: mod.t_onset(5, TA, H), "a series is a sequence of numbers")  # type: ignore[arg-type]
    t, p = market_series()
    c.refuses("text in the proxy", lambda: mod.t_market(t, ["x"] * len(p), TA, H), "a series is a sequence of numbers")  # type: ignore[list-item]
    for label, make, says in (
        ("a lookback below ten minutes", lambda: mod.OnsetParams(lookback=9), "lookback is a whole number of minutes, 10 or more"),
        ("a negative gap", lambda: mod.OnsetParams(sigma_gap=-1), "sigma_gap is a whole number of minutes, 0 or more"),
        ("a pre window that ends where it starts", lambda: mod.OnsetParams(pre_start=5, pre_end=5), "the pre window starts before it ends"),
        ("a negative pre end", lambda: mod.OnsetParams(pre_end=-1), "pre_end is a whole number of minutes, 0 or more"),
        ("a zero move threshold", lambda: mod.OnsetParams(z_move_min=0.0), "a threshold is a positive number"),
        ("a negative pre threshold", lambda: mod.OnsetParams(z_pre_min=-1.0), "a threshold is a positive number"),
        ("a nan post threshold", lambda: mod.OnsetParams(z_post_min=float("nan")), "a threshold is a positive number"),
        ("a text threshold", lambda: mod.OnsetParams(z_post_min="2.5"), "a threshold is a positive number"),  # type: ignore[arg-type]
        ("a market lookback below ten minutes", lambda: mod.MarketParams(lookback=3), "lookback is a whole number of minutes, 10 or more"),
        (
            "a beta window below ten minutes",
            lambda: mod.MarketParams(beta_minutes=9),
            "beta_minutes is a whole number of minutes, 10 or more",
        ),
        ("a zero k", lambda: mod.MarketParams(k=0.0), "a threshold is a positive number"),
    ):
        c.refuses(label, make, says)
    return c.bad


def check_calibration_and_run(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    digest = "a" * 64
    cal = mod.Calibration({"T_ONSET": 0.05, "T_MARKET": 0.0}, 600, digest)
    c.eq("a test within the limit is allowed", (cal.allows("T_ONSET"), cal.allows("T_MARKET")), (True, True))
    c.eq("a test at the limit is allowed", mod.Calibration({"T_ONSET": 0.10}, 1, digest).allows("T_ONSET"), True)
    c.eq("a test just over the limit is not", mod.Calibration({"T_ONSET": 0.1000001}, 1, digest).allows("T_ONSET"), False)
    c.eq("a test with no recorded rate is not", cal.allows("T_PLACEBO_TIME"), False)
    c.eq("the source test needs no rate", cal.allows("T_SOURCE"), True)
    c.eq("an unknown test is not", cal.allows("T_MAGIC"), False)
    c.refuses("a rate above one", lambda: mod.Calibration({"T_ONSET": 1.5}, 1, digest), "a rate is between 0 and 1")
    c.refuses("a negative rate", lambda: mod.Calibration({"T_ONSET": -0.1}, 1, digest), "a rate is between 0 and 1")
    c.refuses("a nan rate", lambda: mod.Calibration({"T_ONSET": float("nan")}, 1, digest), "a rate is between 0 and 1")
    c.refuses(
        "a rate for a test that does not exist", lambda: mod.Calibration({"T_MAGIC": 0.0}, 1, digest), "a rate is for one of the tests"
    )
    c.refuses("no trials", lambda: mod.Calibration({"T_ONSET": 0.0}, 0, digest), "trials is a whole number, 1 or more")
    c.refuses("a boolean number of trials", lambda: mod.Calibration({"T_ONSET": 0.0}, True, digest), "trials is a whole number, 1 or more")  # type: ignore[arg-type]
    c.refuses(
        "a digest that is not hexadecimal",
        lambda: mod.Calibration({"T_ONSET": 0.0}, 1, "zz" * 32),
        "sha256 is 64 lower-case hexadecimal characters",
    )
    c.refuses(
        "a digest in capitals", lambda: mod.Calibration({"T_ONSET": 0.0}, 1, "A" * 64), "sha256 is 64 lower-case hexadecimal characters"
    )

    def res(test: str, label: str, reason: str | None = None) -> Any:
        return mod.Result(test, mod.VERSION, label, reason, {}, {})

    orig, alt, inc = "FAVOURS_ORIGINAL", "FAVOURS_ALTERNATIVE", "INCONCLUSIVE"
    f = mod.run([res("T_ONSET", orig), res("T_MARKET", inc)], cal)
    c.eq("the original survives", (f.outcome, f.cut_by), ("SURVIVED", ()))
    f = mod.run([res("T_ONSET", alt), res("T_MARKET", orig)], cal)
    c.eq("one calibrated test that favours the alternative cuts (the union)", (f.outcome, f.cut_by), ("ALTERNATIVE_FAVOURED", ("T_ONSET",)))
    f = mod.run([res("T_ONSET", alt), res("T_MARKET", alt)], cal)
    c.eq("both cut", f.cut_by, ("T_ONSET", "T_MARKET"))
    f = mod.run([res("T_ONSET", alt)], mod.Calibration({"T_ONSET": 0.5}, 10, digest))
    c.eq("a test that was not calibrated well enough cannot cut", (f.outcome, f.cut_by), ("NOT_COMPLETED", ()))
    c.eq(
        "it is kept in the record as inconclusive, with the reason and what it said",
        [(t.result, t.reason, dict(t.statistics).get("original_result")) for t in f.tests],
        [(inc, "uncalibrated", alt)],
    )
    f = mod.run([res("T_ONSET", alt)], None)
    c.eq("with no calibration nothing cuts", (f.outcome, f.cut_by), ("NOT_COMPLETED", ()))
    f = mod.run([res("T_SOURCE", alt)], None)
    c.eq("the source test needs no calibration to cut", (f.outcome, f.cut_by), ("ALTERNATIVE_FAVOURED", ("T_SOURCE",)))
    f = mod.run([res("T_ONSET", "NOT_TESTABLE", "no_event_time"), res("T_MARKET", "NOT_TESTABLE", "no_event_time")], cal)
    c.eq("all the time-anchored tests untestable is not completed", f.outcome, "NOT_COMPLETED")
    f = mod.run([res("T_ONSET", "NOT_TESTABLE", "no_event_time"), res("T_SOURCE", orig)], cal)
    c.eq("a supported source does not complete a thesis whose time tests were all untestable", f.outcome, "NOT_COMPLETED")
    f = mod.run([res("T_ONSET", "NOT_TESTABLE", "series_too_short"), res("T_MARKET", inc), res("T_SOURCE", orig)], cal)
    c.eq("one testable time-anchored test completes it", f.outcome, "SURVIVED")
    c.eq("what remained open is listed", f.unresolved, ("T_ONSET:series_too_short", "T_MARKET:INCONCLUSIVE"))
    f = mod.run([], cal)
    c.eq("no tests at all is not completed", (f.outcome, f.tests, f.unresolved), ("NOT_COMPLETED", (), ()))
    f = mod.run([res("T_SOURCE", orig)], cal)
    c.eq("a source test alone is not completed", f.outcome, "NOT_COMPLETED")
    c.refuses("the same test twice", lambda: mod.run([res("T_ONSET", orig), res("T_ONSET", inc)], cal), "each test appears once")
    c.refuses("a result with a label that does not exist", lambda: mod.run([res("T_ONSET", "MAYBE")], cal), "a result is one of")
    c.refuses(
        "a result with a reason that does not exist",
        lambda: mod.run([res("T_ONSET", "NOT_TESTABLE", "because")], cal),
        "a reason is one of",
    )
    c.refuses("a result for a test that does not exist", lambda: mod.run([res("T_MAGIC", orig)], cal), "a result is for one of the tests")
    c.refuses(
        "a reason on a result that is testable",
        lambda: mod.run([res("T_ONSET", orig, "no_source")], cal),
        "only a test that cannot be run, or one that was not calibrated, has a reason",
    )
    c.refuses(
        "no reason on a result that cannot be run",
        lambda: mod.run([res("T_ONSET", "NOT_TESTABLE")], cal),
        "a test that cannot be run says why",
    )
    return c.bad


def check_language(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a plain association passes", mod.lint_language("The move followed the announcement and preceded the listing.", False), ())
    c.eq("a banned word is named", mod.lint_language("The listing caused the rise.", False), ("caused",))
    c.eq(
        "each banned phrase",
        mod.lint_language("It rose because of the news, driven by flows, due to a squeeze, and the news caused it.", False),
        ("caused", "because of", "driven by", "due to"),
    )
    c.eq(
        "with the surviving alternatives listed it is allowed",
        mod.lint_language("The listing caused the rise (alternatives: a market move).", True),
        (),
    )
    c.eq(
        "a banned word inside another word is not a hit",
        mod.lint_language("The decaused cause is not a word we use; because it is.", False),
        (),
    )
    c.eq("capitals are caught", mod.lint_language("DUE TO the listing", False), ("due to",))
    c.eq("each phrase is named once", mod.lint_language("caused caused due to due to", False), ("caused", "due to"))
    return c.bad


def check_edges(mod: types.ModuleType) -> list[str]:
    """What is accepted at the edge of every rule, which the refusals alone do not show."""
    c = Case(mod)
    frozen = [mod.OnsetParams, mod.MarketParams, mod.Result, mod.OnsetScores, mod.MarketScores, mod.Calibration, mod.Falsification]
    c.eq("the records are frozen", [k.__dataclass_params__.frozen for k in frozen], [True] * len(frozen))
    c.eq(
        "the smallest lookback and gap",
        (
            mod.OnsetParams(lookback=10, sigma_gap=0, pre_end=0).lookback,
            mod.MarketParams(lookback=10, sigma_gap=0, beta_minutes=10).beta_minutes,
        ),
        (10, 10),
    )
    c.eq("a threshold below one is allowed", (mod.OnsetParams(z_move_min=0.5).z_move_min, mod.MarketParams(k=0.5).k), (0.5, 0.5))
    c.eq("a threshold of one is allowed", mod.MarketParams(k=1.0).k, 1.0)
    c.refuses("a negative market gap", lambda: mod.MarketParams(sigma_gap=-1), "sigma_gap is a whole number of minutes, 0 or more")
    c.eq(
        "an announcement at minute zero is accepted and cannot be tested",
        (mod.t_onset(returns(), 0, H).result, mod.t_onset(returns(), 0, H).reason),
        ("NOT_TESTABLE", "series_too_short"),
    )
    c.eq("a one-minute horizon is accepted", mod.t_onset(returns(h=1), TA, 1).reason, None)
    t, p = market_series()
    c.refuses("a negative announcement minute in the market test", lambda: mod.t_market(t, p, -1, H), "ta is a whole number of minutes")
    c.refuses("a zero horizon in the market test", lambda: mod.t_market(t, p, TA, 0), "h is a whole number of minutes")
    c.eq("the median of an even count is the mean of the middle two", mod._median([4.0, 1.0, 3.0, 2.0]), 2.5)
    c.eq("the median of an odd count is the middle one", mod._median([5.0, 1.0, 3.0]), 3.0)
    c.eq("the median of unsorted values", mod._median([9.0, 1.0, 5.0, 7.0, 3.0]), 5.0)
    c.eq(
        "the exception carries its reason",
        (str(mod.NotTestable("no_source")), mod.NotTestable("no_source").reason),
        ("no_source", "no_source"),
    )
    inc = "INCONCLUSIVE"
    c.eq(
        "an inconclusive result may say it was not calibrated",
        mod.Result("T_ONSET", mod.VERSION, inc, "uncalibrated", {}, {}).reason,
        "uncalibrated",
    )
    c.refuses(
        "an inconclusive result with another reason",
        lambda: mod.Result("T_ONSET", mod.VERSION, inc, "no_source", {}, {}),
        "only a test that cannot be run",
    )
    c.refuses(
        "a decided result that says it was not calibrated",
        lambda: mod.Result("T_ONSET", mod.VERSION, "FAVOURS_ORIGINAL", "uncalibrated", {}, {}),
        "only a test that cannot be run",
    )
    # the volatility is a robust one even when the block's median is not zero
    c.near("sigma of a block that sits above zero", mod.onset_scores(returns(offset=0.01), TA, H, mod.OnsetParams()).sigma, SIGMA)
    c.near("sigma of a block that sits below zero", mod.onset_scores(returns(offset=-0.01), TA, H, mod.OnsetParams()).sigma, SIGMA)
    # a small drift in the same direction counts when its threshold is small: the sign of a small score is its sign
    small = mod.OnsetParams(z_move_min=0.1, z_pre_min=0.1, z_post_min=0.1)
    c.eq(
        "a pre drift of half a unit, in the direction of the move, counts with a small threshold",
        mod.t_onset(returns(pre=drift_for_z(0.5, 55), post=drift_for_z(4, H)), TA, H, small).result,
        "FAVOURS_ALTERNATIVE",
    )
    c.eq(
        "a small pre drift against the move does not",
        mod.t_onset(returns(pre=-drift_for_z(0.5, 55), post=drift_for_z(4, H)), TA, H, small).result,
        "FAVOURS_ORIGINAL",
    )
    c.eq(
        "a small post drift against the move does not carry it",
        mod.t_onset(
            returns(pre=drift_for_z(4, 55), post=-drift_for_z(0.5, H)), TA, H, mod.OnsetParams(z_pre_min=9.0, z_post_min=0.1)
        ).result,
        "INCONCLUSIVE",
    )
    # the market windows are where they are said to be, on a series that is not stationary
    t, p = drifting_market()
    s = mod.market_scores(t, p, TA, H, mod.MarketParams(beta_minutes=448))
    c.near("beta is estimated over the 448 minutes that end 120 before the announcement", s.beta, 1.2, 1e-9)
    c.near("the residual's noise is measured over the 360 minutes that end there", s.sigma_residual, 1.4826 * 0.0005)
    c.near("so is the token's own", s.sigma_raw, 1.4826 * 0.0012)
    t, p = drifting_market(ta=700, lookback=500, beta_minutes=448)
    s = mod.market_scores(t, p, 700, H, mod.MarketParams(lookback=500, beta_minutes=448))
    c.near("a longer lookback than beta window: the beta window is still the last 448 minutes", s.beta, 1.2, 1e-9)
    c.near("and the volatility block is the 500", s.sigma_residual, 1.4826 * 0.0005)
    t, p = drifting_market(lookback=360, beta_minutes=448)
    s = mod.market_scores(t, p, TA, H, mod.MarketParams(lookback=360, beta_minutes=448, sigma_gap=120))
    t, p = drifting_market(big_first=200)
    s = mod.market_scores(t, p, TA, H, mod.MarketParams(beta_minutes=448))
    c.near("the volatility block starts exactly 360 minutes before its end", s.sigma_residual, 1.4826 * 0.001)
    t, p = drifting_market()
    t2 = [x + 0.003 for x in t]
    p2 = [x + 0.002 for x in p]
    c.near(
        "a constant added to both series changes no beta",
        mod.market_scores(t2, p2, TA, H, mod.MarketParams(beta_minutes=448)).beta,
        1.2,
        1e-9,
    )
    c.near(
        "a constant added to the token changes no volatility",
        mod.market_scores(t2, p2, TA, H, mod.MarketParams(beta_minutes=448)).sigma_raw,
        1.4826 * 0.0012,
        1e-9,
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (
        check_constants,
        check_onset_scores,
        check_t_onset,
        check_not_testable,
        check_market,
        check_source_and_placebo,
        check_refusals,
        check_calibration_and_run,
        check_language,
        check_edges,
    )
    for check in checks:
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
