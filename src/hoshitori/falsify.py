"""Deterministic tests that try to disprove a call before it is made (``FALSIFICATION_PROTOCOL``, contract section 48).

Each test is a pure function of stored one-minute return series and registered parameters: no model, no clock and no randomness. A result is
one of ``FAVOURS_ORIGINAL``, ``FAVOURS_ALTERNATIVE``, ``INCONCLUSIVE`` or ``NOT_TESTABLE`` with a reason from a closed list; data that is
missing is ``NOT_TESTABLE``, never skipped and never guessed.

* ``T_ONSET`` is the one-look rule of experiment E7: the whole window from 60 minutes before the announcement to the horizon must hold a
  move of at least three local standard deviations; if the minutes before the announcement already carry a same-direction drift of at least
  2.5 the move began earlier (the alternative), and if the minutes after carry it the announcement is where it began (the original).
  One look per segment, because a running maximum over a long window flagged an earlier start in a quarter of the planted trials of a true
  effect.
* ``T_MARKET`` is E7's beta test: the token's beta on a market proxy is estimated over the 450 minutes that end 120 before the announcement
  (the window E7 was calibrated on), and the residual move over the horizon is compared with its own local noise; a small residual under a
  large raw move means the market explains it.
* ``T_SOURCE`` takes the support label of the grounding layers.
* ``T_PLACEBO_TIME`` is left out: the specified test is not the one that was calibrated, so it is ``NOT_TESTABLE`` as uncalibrated.

A test may reduce exposure only if a recorded calibration on planted true effects shows a false-alternative rate of at most 10 percent
(``Calibration``); a test with none recorded keeps its result visible but cannot cut. The tests are associations and say nothing about
cause (``lint_language``). Statistics are computed in binary floating point with exactly rounded sums (``math.fsum``) and a fixed order of
operations, so a result is the same on any machine, and are recorded as text.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

LABELS: Final = ("FAVOURS_ORIGINAL", "FAVOURS_ALTERNATIVE", "INCONCLUSIVE", "NOT_TESTABLE")
OUTCOMES: Final = ("ALTERNATIVE_FAVOURED", "SURVIVED", "NOT_COMPLETED")
TESTS: Final = ("T_ONSET", "T_MARKET", "T_SOURCE", "T_PLACEBO_TIME")
TIME_ANCHORED: Final = frozenset({"T_ONSET", "T_MARKET", "T_PLACEBO_TIME"})
REASONS: Final = (
    "series_too_short",
    "series_misaligned",
    "non_finite_value",
    "no_volatility",
    "proxy_has_no_variation",
    "no_event_time",
    "uncalibrated",
    "no_source",
)
SUPPORT_LABELS: Final = ("SUPPORTED", "CONTRADICTED", "NOT_SUPPORTED", "UNVERIFIED")
CAUSAL_WORDS: Final = ("caused", "because of", "driven by", "due to")
MAX_FALSE_ALTERNATIVE: Final = 0.10
VERSION: Final = "falsify/1"
MAD_TO_SIGMA: Final = 1.4826  # the scale that makes the median absolute deviation estimate a normal standard deviation


class FalsifyError(ValueError):
    """An argument that is not valid: the caller's mistake, never a property of the data."""


class NotTestable(Exception):
    """The data cannot support the test; ``reason`` is one of ``REASONS``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _minutes(value: object, name: str, floor: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < floor:
        raise FalsifyError(f"{name} is a whole number of minutes, {floor} or more")


def _threshold(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise FalsifyError("a threshold is a positive number")


@dataclass(frozen=True)
class OnsetParams:
    lookback: int = 360
    sigma_gap: int = 120
    pre_start: int = 60
    pre_end: int = 5
    z_move_min: float = 3.0
    z_pre_min: float = 2.5
    z_post_min: float = 2.5

    def __post_init__(self) -> None:
        _minutes(self.lookback, "lookback", 10)
        _minutes(self.sigma_gap, "sigma_gap", 0)
        _minutes(self.pre_end, "pre_end", 0)
        if not isinstance(self.pre_start, int) or isinstance(self.pre_start, bool) or self.pre_start <= self.pre_end:
            raise FalsifyError("the pre window starts before it ends")
        for value in (self.z_move_min, self.z_pre_min, self.z_post_min):
            _threshold(value)


@dataclass(frozen=True)
class MarketParams:
    lookback: int = 360
    sigma_gap: int = 120
    beta_minutes: int = 450
    k: float = 3.0

    def __post_init__(self) -> None:
        _minutes(self.lookback, "lookback", 10)
        _minutes(self.sigma_gap, "sigma_gap", 0)
        _minutes(self.beta_minutes, "beta_minutes", 10)
        _threshold(self.k)


@dataclass(frozen=True)
class Result:
    """One test's result, with the numbers it was decided on as text."""

    test: str
    version: str
    result: str
    reason: str | None
    parameters: Mapping[str, str]
    statistics: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.test not in TESTS:
            raise FalsifyError(f"a result is for one of the tests {TESTS}")
        if self.result not in LABELS:
            raise FalsifyError(f"a result is one of {LABELS}")
        if self.result == "NOT_TESTABLE":
            if self.reason is None:
                raise FalsifyError("a test that cannot be run says why")
            if self.reason not in REASONS:
                raise FalsifyError(f"a reason is one of {REASONS}")
        elif self.reason is not None and not (self.result == "INCONCLUSIVE" and self.reason == "uncalibrated"):
            raise FalsifyError("only a test that cannot be run, or one that was not calibrated, has a reason")


@dataclass(frozen=True)
class OnsetScores:
    sigma: float
    z_total: float
    z_pre: float
    z_post: float


@dataclass(frozen=True)
class MarketScores:
    beta: float
    sigma_residual: float
    sigma_raw: float
    z_residual: float
    z_raw: float


def _series(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise FalsifyError("a series is a sequence of numbers")
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not (isinstance(item, (int, float)) or _is_decimal(item)):
            raise FalsifyError("a series is a sequence of numbers")
        out.append(float(item))
    return out


def _is_decimal(item: object) -> bool:
    from decimal import Decimal

    return isinstance(item, Decimal)


def _event(ta: object, h: object) -> tuple[int, int]:
    if isinstance(ta, bool) or not isinstance(ta, int) or ta < 0:
        raise FalsifyError("ta is a whole number of minutes, 0 or more")
    if isinstance(h, bool) or not isinstance(h, int) or h < 1:
        raise FalsifyError("h is a whole number of minutes, 1 or more")
    return ta, h


def _read(series: list[float], start: int, stop: int) -> list[float]:
    """The values ``series[start:stop]``, or the reason they cannot be used."""
    if start < 0 or len(series) < stop:
        raise NotTestable("series_too_short")
    part = series[start:stop]
    if not all(math.isfinite(x) for x in part):
        raise NotTestable("non_finite_value")
    return part


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _sigma(block: list[float]) -> float:
    centre = _median(block)
    sigma = MAD_TO_SIGMA * _median([abs(x - centre) for x in block])
    if sigma == 0.0:
        raise NotTestable("no_volatility")
    return sigma


def onset_scores(returns: Sequence[float], ta: int, h: int, params: OnsetParams) -> OnsetScores:
    """The scores ``T_ONSET`` decides on: z over the whole window, over the minutes before the announcement and over the horizon."""
    ta, h = _event(ta, h)
    series = _series(returns)
    block_end = ta - params.sigma_gap
    block = _read(series, block_end - params.lookback, block_end)
    window = _read(series, ta - params.pre_start, ta + h)
    sigma = _sigma(block)
    pre_len = params.pre_start - params.pre_end
    pre = window[:pre_len]
    post = window[params.pre_start :]
    return OnsetScores(
        sigma,
        math.fsum(window) / (sigma * math.sqrt(params.pre_start + h)),
        math.fsum(pre) / (sigma * math.sqrt(pre_len)),
        math.fsum(post) / (sigma * math.sqrt(h)),
    )


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _text(x: float) -> str:
    return f"{x:.6f}"


def t_onset(returns: Sequence[float], ta: int, h: int, params: OnsetParams | None = None) -> Result:
    params = params or OnsetParams()
    record = {
        "lookback": str(params.lookback),
        "sigma_gap": str(params.sigma_gap),
        "pre_start": str(params.pre_start),
        "pre_end": str(params.pre_end),
        "z_move_min": str(params.z_move_min),
        "z_pre_min": str(params.z_pre_min),
        "z_post_min": str(params.z_post_min),
        "ta": str(ta),
        "h": str(h),
    }
    try:
        s = onset_scores(returns, ta, h, params)
    except NotTestable as exc:
        return Result("T_ONSET", VERSION, "NOT_TESTABLE", exc.reason, record, {})
    stats = {"sigma": f"{s.sigma:.8f}", "z_total": _text(s.z_total), "z_pre": _text(s.z_pre), "z_post": _text(s.z_post)}
    if abs(s.z_total) < params.z_move_min:
        label = "INCONCLUSIVE"  # no move to date
    elif abs(s.z_pre) >= params.z_pre_min and _sign(s.z_pre) == _sign(s.z_total):
        label = "FAVOURS_ALTERNATIVE"  # the move began before the announcement
    elif abs(s.z_post) >= params.z_post_min and _sign(s.z_post) == _sign(s.z_total):
        label = "FAVOURS_ORIGINAL"  # the minutes after carry it
    else:
        label = "INCONCLUSIVE"
    return Result("T_ONSET", VERSION, label, None, record, stats)


def market_scores(token: Sequence[float], proxy: Sequence[float], ta: int, h: int, params: MarketParams) -> MarketScores:
    ta, h = _event(ta, h)
    tok, prox = _series(token), _series(proxy)
    if len(tok) != len(prox):
        raise NotTestable("series_misaligned")
    block_end = ta - params.sigma_gap
    start = block_end - max(params.beta_minutes, params.lookback)
    tok_read, prox_read = _read(tok, start, ta + h), _read(prox, start, ta + h)
    offset = block_end - params.beta_minutes - start
    x = prox_read[offset : offset + params.beta_minutes]
    y = tok_read[offset : offset + params.beta_minutes]
    if max(x) == min(x):
        raise NotTestable("proxy_has_no_variation")
    mx, my = math.fsum(x) / len(x), math.fsum(y) / len(y)
    beta = math.fsum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / math.fsum((a - mx) ** 2 for a in x)
    residual = [t - beta * p for t, p in zip(tok_read, prox_read, strict=True)]
    block_from = block_end - params.lookback - start
    block_to = block_end - start
    sigma_residual = _sigma(residual[block_from:block_to])
    sigma_raw = _sigma(tok_read[block_from:block_to])
    event = ta - start
    return MarketScores(
        beta,
        sigma_residual,
        sigma_raw,
        math.fsum(residual[event:]) / (sigma_residual * math.sqrt(h)),
        math.fsum(tok_read[event:]) / (sigma_raw * math.sqrt(h)),
    )


def t_market(token: Sequence[float], proxy: Sequence[float], ta: int, h: int, params: MarketParams | None = None) -> Result:
    params = params or MarketParams()
    record = {
        "lookback": str(params.lookback),
        "sigma_gap": str(params.sigma_gap),
        "beta_minutes": str(params.beta_minutes),
        "k": str(params.k),
        "ta": str(ta),
        "h": str(h),
    }
    try:
        s = market_scores(token, proxy, ta, h, params)
    except NotTestable as exc:
        return Result("T_MARKET", VERSION, "NOT_TESTABLE", exc.reason, record, {})
    stats = {
        "beta": f"{s.beta:.6f}",
        "sigma_residual": f"{s.sigma_residual:.8f}",
        "sigma_raw": f"{s.sigma_raw:.8f}",
        "z_residual": _text(s.z_residual),
        "z_raw": _text(s.z_raw),
    }
    if abs(s.z_residual) >= params.k:
        label = "FAVOURS_ORIGINAL"  # the token moved beyond what the market explains
    elif abs(s.z_raw) >= params.k:
        label = "FAVOURS_ALTERNATIVE"  # a large raw move with a small residual: the market explains it
    else:
        label = "INCONCLUSIVE"
    return Result("T_MARKET", VERSION, label, None, record, stats)


_SUPPORT_RESULT: Final = {
    "SUPPORTED": "FAVOURS_ORIGINAL",
    "CONTRADICTED": "FAVOURS_ALTERNATIVE",
    "NOT_SUPPORTED": "FAVOURS_ALTERNATIVE",
    "UNVERIFIED": "INCONCLUSIVE",
}


def t_source(support: str | None) -> Result:
    """The cited source supports the interpretation, from the support label of the grounding layers. An unverified support is
    inconclusive: it never favours the original."""
    if support is None:
        return Result("T_SOURCE", VERSION, "NOT_TESTABLE", "no_source", {}, {})
    if support not in SUPPORT_LABELS:
        raise FalsifyError(f"support is one of {SUPPORT_LABELS}")
    return Result("T_SOURCE", VERSION, _SUPPORT_RESULT[support], None, {}, {"support": support})


def t_placebo_time() -> Result:
    """Left out: the specified test (placebo times in the preceding history) is not the test that was calibrated."""
    return Result("T_PLACEBO_TIME", VERSION, "NOT_TESTABLE", "uncalibrated", {}, {})


def without_event_time() -> tuple[Result, Result]:
    """A candidate that came from the registry has no announcement time (the first time it was seen is later than the event), so the
    time-anchored tests cannot be run."""
    nt = "NOT_TESTABLE"
    return (Result("T_ONSET", VERSION, nt, "no_event_time", {}, {}), Result("T_MARKET", VERSION, nt, "no_event_time", {}, {}))


@dataclass(frozen=True)
class Calibration:
    """The false-alternative rate of each test on planted true effects, with how many trials it was measured on and the digest of the
    report it came from."""

    rates: Mapping[str, float]
    trials: int
    sha256: str

    def __post_init__(self) -> None:
        for test, rate in self.rates.items():
            if test not in TESTS:
                raise FalsifyError(f"a rate is for one of the tests {TESTS}")
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0.0 <= rate <= 1.0:
                raise FalsifyError("a rate is between 0 and 1")
        if isinstance(self.trials, bool) or not isinstance(self.trials, int) or self.trials < 1:
            raise FalsifyError("trials is a whole number, 1 or more")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise FalsifyError("sha256 is 64 lower-case hexadecimal characters")

    def allows(self, test: str) -> bool:
        """Whether a result of this test may reduce exposure."""
        if test == "T_SOURCE":
            return True  # a support label, not a statistic: nothing to calibrate
        rate = self.rates.get(test)
        return rate is not None and rate <= MAX_FALSE_ALTERNATIVE


@dataclass(frozen=True)
class Falsification:
    outcome: str
    tests: tuple[Result, ...]
    cut_by: tuple[str, ...]
    unresolved: tuple[str, ...]


def _allowed(calibration: Calibration | None, test: str) -> bool:
    """Whether a result of ``test`` may reduce exposure: the source test always, any other only with a good enough recorded rate."""
    if test == "T_SOURCE":
        return True
    return calibration is not None and calibration.allows(test)


def run(results: Sequence[Result], calibration: Calibration | None) -> Falsification:
    """Combine the results: the union gate. A test that favours the alternative cuts only if it is calibrated; otherwise it stays in the
    record as inconclusive with the reason and what it said. The thesis is ``NOT_COMPLETED`` unless at least one calibrated time-anchored
    test could be run."""
    seen: set[str] = set()
    for r in results:
        if r.test in seen:
            raise FalsifyError("each test appears once")
        seen.add(r.test)
    effective: list[Result] = []
    for r in results:
        if r.result == "FAVOURS_ALTERNATIVE" and not _allowed(calibration, r.test):
            r = Result(r.test, r.version, "INCONCLUSIVE", "uncalibrated", r.parameters, {**r.statistics, "original_result": r.result})
        effective.append(r)
    cut_by = tuple(r.test for r in effective if r.result == "FAVOURS_ALTERNATIVE")
    counted = [r for r in effective if r.test in TIME_ANCHORED and r.result != "NOT_TESTABLE" and _allowed(calibration, r.test)]
    if cut_by:
        outcome = "ALTERNATIVE_FAVOURED"
    elif not counted:
        outcome = "NOT_COMPLETED"
    else:
        outcome = "SURVIVED"
    unresolved = tuple(f"{r.test}:{r.reason or r.result}" for r in effective if r.result in ("NOT_TESTABLE", "INCONCLUSIVE"))
    return Falsification(outcome, tuple(effective), cut_by, unresolved)


def lint_language(text: str, alternatives_listed: bool) -> tuple[str, ...]:
    """The causal words in ``text``. They are allowed only where the surviving alternatives are listed beside them."""
    if alternatives_listed:
        return ()
    lowered = text.lower()
    return tuple(word for word in CAUSAL_WORDS if re.search(rf"\b{re.escape(word)}\b", lowered))
