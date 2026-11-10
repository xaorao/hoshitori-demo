"""Scores of settled forecasts: the proper scores, the two fixed references, coverage, exclusions and shadow trades.

Contract sections 2, 3, 5, 7 and 10 are the rules. Everything here is a pure function of values the caller passes in: it reads no
clock, no file and no ledger, and it never chooses which forecasts are compared (the pre-registered population does). Arithmetic is
decimal with 60 digits and half-even rounding, so a score does not depend on the machine; a score is reported to twelve places.

* **Scores.** Contract D: Brier ``(p - o)^2`` and log loss ``-ln(p or 1 - p)`` on the probability clipped to [0.01, 0.99]. Contract
  B: the three-class Brier ``sum (p_i - o_i)^2`` over TARGET, STOP and TIMEOUT, whose probabilities must sum to exactly one.
  Lower is better for all three.
* **Skill.** ``1 - mean score / mean reference score``, against the fixed forecast 0.5 and against the climatological rate of the
  30 days before the forecast was created, never later data. A reference that is not available is absent, never zero.
* **Coverage.** Failures are not abstentions: coverage is calls over calls plus voluntary no-calls, availability is one minus
  failures over everything eligible.
* **Exclusions are published with bounds.** Every VOID, UNSETTLEABLE and AMBIGUOUS forecast is counted by arm, asset, regime and
  reason, and each score is recomputed with every excluded forecast counted as its best and as its worst possible outcome. A
  skill that changes sign inside those bounds is sensitive to the exclusions. A barrier forecast excluded as AMBIGUOUS (its reason)
  touched both levels in one minute, so its possible outcomes are TARGET and STOP, never TIMEOUT (contract section 3); for any
  other reason all three are possible.
* **Shadow trades** are hypothetical and are only counted here: the denominator is every voluntary abstain.
"""

from __future__ import annotations

import functools
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Final

PRECISION: Final = 60
PROB_FLOOR: Final = Decimal("0.01")
PROB_CEIL: Final = Decimal("0.99")
HALF: Final = Decimal("0.5")
REPORT_PLACES: Final = 12
DAY_MS: Final = 86_400_000
CLIMATE_DAYS: Final = 30
CLIMATE_MIN: Final = 20  # a prior: fewer settled outcomes in the window and the reference is not available
BARRIER_CLASSES: Final = ("TARGET", "STOP", "TIMEOUT")
AMBIGUOUS_OUTCOMES: Final = ("TARGET", "STOP")  # a minute that touched both levels reached one of them first: it never timed out
# The module's own decimal context, never the caller's: its precision, its rounding and its traps (an inexact result such as a
# logarithm is rounded, never raised), so a caller that is narrower, rounds another way or traps an inexact result gets the same scores.
_CTX: Final = Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)


class ScoringError(ValueError):
    """An input no score can be computed from."""


def _exact[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext(_CTX):
            return function(*args, **kwargs)

    return wrapped


def shown(value: Decimal) -> str:
    """A score as the pages print it: twelve places, half to even, plain digits."""
    with localcontext(_CTX):
        return format(value.quantize(Decimal(10) ** -REPORT_PLACES, rounding=ROUND_HALF_EVEN), "f")


def _probability(p: Decimal) -> Decimal:
    if not isinstance(p, Decimal) or not p.is_finite() or p < 0 or p > 1:
        raise ScoringError("a probability is a decimal from 0 to 1")
    return p


def _label(value: str, what: str) -> None:
    if not isinstance(value, str) or not value:
        raise ScoringError(f"{what} is a non-empty string")


def _binary(outcome: int) -> int:
    if not isinstance(outcome, int) or isinstance(outcome, bool) or outcome not in (0, 1):
        raise ScoringError("an outcome is the whole number 0 or 1")
    return outcome


def clip(p: Decimal) -> Decimal:
    return min(max(_probability(p), PROB_FLOOR), PROB_CEIL)


# --- the scores of one forecast ---------------------------------------------------------------------------------------------------------
@_exact
def brier(p: Decimal, outcome: int) -> Decimal:
    return (clip(p) - _binary(outcome)) ** 2


@_exact
def log_loss(p: Decimal, outcome: int) -> Decimal:
    q = clip(p)
    return -(q if _binary(outcome) == 1 else 1 - q).ln()


@_exact
def _distribution(probabilities: Sequence[Decimal]) -> list[Decimal]:
    if len(probabilities) != len(BARRIER_CLASSES):
        raise ScoringError("a barrier forecast has three probabilities")
    parts = [_probability(p) for p in probabilities]
    if sum(parts, Decimal(0)) != 1:
        raise ScoringError("the three probabilities sum to exactly one")
    return parts


@_exact
def brier3(probabilities: Sequence[Decimal], outcome: str) -> Decimal:
    """Three-class Brier over TARGET, STOP and TIMEOUT; the probabilities are exact decimals that sum to one."""
    parts = _distribution(probabilities)
    if outcome not in BARRIER_CLASSES:
        raise ScoringError("a barrier outcome is TARGET, STOP or TIMEOUT")
    return sum(((p - (1 if BARRIER_CLASSES[i] == outcome else 0)) ** 2 for i, p in enumerate(parts)), Decimal(0))


# --- the fixed references ---------------------------------------------------------------------------------------------------------------
@_exact
def climatology(history: Iterable[tuple[int, int]], created_at_ms: int, minimum: int = CLIMATE_MIN) -> Decimal | None:
    """The share of rises among outcomes that became known in the 30 days before ``created_at_ms``, or ``None`` if too few.

    ``history`` is pairs of the millisecond an outcome became known and the outcome (0 or 1). An outcome known at or after the
    creation time is later data and is never read. Which forecasts make up the history (same contract, horizon, assets) is the
    caller's pre-registered choice, not this function's.
    """
    if isinstance(created_at_ms, bool) or not isinstance(created_at_ms, int) or created_at_ms < 0 or minimum < 1:
        raise ScoringError("a creation time is a whole number of milliseconds and the minimum is at least one")
    start = created_at_ms - CLIMATE_DAYS * DAY_MS
    outcomes = []
    for known_at, outcome in history:
        if isinstance(known_at, bool) or not isinstance(known_at, int):
            raise ScoringError("the time an outcome became known is a whole number of milliseconds")
        _binary(outcome)  # every entry is checked, also those outside the window
        if start <= known_at < created_at_ms:
            outcomes.append(outcome)
    if len(outcomes) < minimum:
        return None
    return Decimal(sum(outcomes)) / len(outcomes)


# --- settled and excluded forecasts -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Directional:
    """A settled directional forecast: the probability it committed, what happened, and the climatological rate before it."""

    forecast_id: str
    arm: str
    asset: str
    regime: str
    p: Decimal
    outcome: int
    climate: Decimal | None = None

    def __post_init__(self) -> None:
        for value, what in ((self.forecast_id, "an id"), (self.arm, "an arm"), (self.asset, "an asset"), (self.regime, "a regime")):
            _label(value, what)
        _probability(self.p)
        _binary(self.outcome)
        if self.climate is not None:
            _probability(self.climate)


@dataclass(frozen=True)
class ExcludedDirectional:
    """A directional forecast that could not be scored (VOID, UNSETTLEABLE) with its reason."""

    forecast_id: str
    arm: str
    asset: str
    regime: str
    reason: str
    p: Decimal
    climate: Decimal | None = None

    def __post_init__(self) -> None:
        for value, what in (
            (self.forecast_id, "an id"),
            (self.arm, "an arm"),
            (self.asset, "an asset"),
            (self.regime, "a regime"),
            (self.reason, "a reason"),
        ):
            _label(value, what)
        _probability(self.p)
        if self.climate is not None:
            _probability(self.climate)


@dataclass(frozen=True)
class Barrier:
    forecast_id: str
    arm: str
    asset: str
    regime: str
    probabilities: tuple[Decimal, Decimal, Decimal]
    outcome: str

    def __post_init__(self) -> None:
        for value, what in ((self.forecast_id, "an id"), (self.arm, "an arm"), (self.asset, "an asset"), (self.regime, "a regime")):
            _label(value, what)
        brier3(self.probabilities, self.outcome)


@dataclass(frozen=True)
class ExcludedBarrier:
    forecast_id: str
    arm: str
    asset: str
    regime: str
    reason: str
    probabilities: tuple[Decimal, Decimal, Decimal]

    def __post_init__(self) -> None:
        for value, what in (
            (self.forecast_id, "an id"),
            (self.arm, "an arm"),
            (self.asset, "an asset"),
            (self.regime, "a regime"),
            (self.reason, "a reason"),
        ):
            _label(value, what)
        _distribution(self.probabilities)


# --- summaries --------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Skill:
    """The mean score and mean reference score over the same forecasts, and ``1 - score / reference``."""

    n: int
    score: Decimal
    reference: Decimal
    skill: Decimal


@dataclass(frozen=True)
class DirectionalSummary:
    n: int
    brier: Decimal
    log_loss: Decimal
    brier_vs_half: Skill
    log_vs_half: Skill
    brier_vs_climate: Skill | None
    log_vs_climate: Skill | None


@_exact
def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


@_exact
def _skill(scores: Sequence[Decimal], references: Sequence[Decimal]) -> Skill:
    mean_score, mean_reference = _mean(scores), _mean(references)
    return Skill(len(scores), mean_score, mean_reference, 1 - mean_score / mean_reference)


@_exact
def summarize_directional(rows: Sequence[Directional]) -> DirectionalSummary:
    if not rows:
        raise ScoringError("nothing to summarise")
    briers = [brier(r.p, r.outcome) for r in rows]
    losses = [log_loss(r.p, r.outcome) for r in rows]
    half_brier = [brier(HALF, r.outcome) for r in rows]
    half_loss = [log_loss(HALF, r.outcome) for r in rows]
    # a missing reference is absent: the comparison uses only the forecasts that have one, on both sides
    with_climate = [(r, r.climate) for r in rows if r.climate is not None]
    climate_brier = climate_loss = None
    if with_climate:
        climate_brier = _skill([brier(r.p, r.outcome) for r, _ in with_climate], [brier(q, r.outcome) for r, q in with_climate])
        climate_loss = _skill([log_loss(r.p, r.outcome) for r, _ in with_climate], [log_loss(q, r.outcome) for r, q in with_climate])
    return DirectionalSummary(
        len(rows), _mean(briers), _mean(losses), _skill(briers, half_brier), _skill(losses, half_loss), climate_brier, climate_loss
    )


@dataclass(frozen=True)
class BarrierSummary:
    n: int
    brier3: Decimal
    counts: tuple[int, int, int]


@_exact
def summarize_barrier(rows: Sequence[Barrier]) -> BarrierSummary:
    if not rows:
        raise ScoringError("nothing to summarise")
    counted = Counter(r.outcome for r in rows)
    return BarrierSummary(
        len(rows),
        _mean([brier3(r.probabilities, r.outcome) for r in rows]),
        (counted["TARGET"], counted["STOP"], counted["TIMEOUT"]),
    )


# --- reliability ------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReliabilityBin:
    count: int
    mean_p: Decimal
    frequency: Decimal
    low: Decimal
    high: Decimal


@_exact
def reliability_bins(rows: Sequence[Directional], bins: int) -> tuple[ReliabilityBin, ...]:
    """Equal-count bins by the clipped probability, ties broken by forecast id; sizes differ by at most one, larger bins first."""
    if bins < 1 or len(rows) < bins:
        raise ScoringError("there are at least as many forecasts as bins, and at least one bin")
    ordered = sorted(rows, key=lambda r: (clip(r.p), r.forecast_id))
    base, extra = divmod(len(ordered), bins)
    out: list[ReliabilityBin] = []
    start = 0
    for index in range(bins):
        size = base + (1 if index < extra else 0)
        chunk = ordered[start : start + size]
        start += size
        probabilities = [clip(r.p) for r in chunk]
        out.append(
            ReliabilityBin(size, _mean(probabilities), Decimal(sum(r.outcome for r in chunk)) / size, probabilities[0], probabilities[-1])
        )
    return tuple(out)


# --- coverage, selective risk, availability ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CoverageReport:
    calls: int
    no_calls: int
    failed: int
    eligible: int
    coverage: Decimal | None
    availability: Decimal | None


@_exact
def coverage(calls: int, no_calls: int, failed: int) -> CoverageReport:
    """Failures are operational and never abstentions: they lower availability and leave coverage alone."""
    for count in (calls, no_calls, failed):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ScoringError("a count is a whole number of zero or more")
    decided = calls + no_calls
    eligible = decided + failed
    return CoverageReport(
        calls,
        no_calls,
        failed,
        eligible,
        Decimal(calls) / decided if decided else None,
        1 - Decimal(failed) / eligible if eligible else None,
    )


# --- exclusions -------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ExclusionCount:
    arm: str
    asset: str
    regime: str
    reason: str
    count: int


def exclusion_table(excluded: Iterable[ExcludedDirectional | ExcludedBarrier]) -> tuple[ExclusionCount, ...]:
    """Every exclusion counted by arm, asset, regime and reason, in a fixed order."""
    counted = Counter((e.arm, e.asset, e.regime, e.reason) for e in excluded)
    return tuple(ExclusionCount(*key, count) for key, count in sorted(counted.items()))


@dataclass(frozen=True)
class Bounds:
    """A figure on the settled forecasts, and the least and greatest it could be if every excluded forecast had gone either way."""

    headline: Decimal
    lower: Decimal
    upper: Decimal

    @property
    def changes_sign(self) -> bool:
        """For a skill: among the headline and its two bounds there is a positive value and a negative one."""
        values = (self.headline, self.lower, self.upper)
        return min(values) < 0 < max(values)


@_exact
def _ratio_extreme(fixed: Sequence[tuple[Decimal, Decimal]], free: Sequence[Sequence[tuple[Decimal, Decimal]]], smallest: bool) -> Decimal:
    """The least or greatest total score over total reference, each free forecast taking the way it ends that suits.

    A fractional program: for a trial ratio each forecast independently picks the option that minimises (or maximises) score
    minus ratio times reference; the ratio of those picks becomes the trial, until it stops improving (Dinkelbach).
    """
    pick = min if smallest else max
    base_score = sum((s for s, _ in fixed), Decimal(0))
    base_reference = sum((r for _, r in fixed), Decimal(0))

    def total(trial: Decimal) -> Decimal:
        score, reference = base_score, base_reference
        for options in free:
            chosen = pick(options, key=lambda option: option[0] - trial * option[1])
            score += chosen[0]
            reference += chosen[1]
        return score / reference

    # Each round moves to a different selection with a strictly better ratio, and there are finitely many selections, so it ends.
    trial = total(Decimal(0))
    while True:
        better = total(trial)
        if (better >= trial) if smallest else (better <= trial):
            return trial
        trial = better


@_exact
def _mean_bounds(fixed: Sequence[Decimal], free: Sequence[Sequence[Decimal]]) -> Bounds:
    total = len(fixed) + len(free)
    base = sum(fixed, Decimal(0))
    return Bounds(
        _mean(fixed),
        (base + sum((min(options) for options in free), Decimal(0))) / total,
        (base + sum((max(options) for options in free), Decimal(0))) / total,
    )


@_exact
def _skill_bounds(fixed: Sequence[tuple[Decimal, Decimal]], free: Sequence[Sequence[tuple[Decimal, Decimal]]]) -> Bounds:
    mean_score = _mean([s for s, _ in fixed])
    mean_reference = _mean([r for _, r in fixed])
    return Bounds(
        1 - mean_score / mean_reference,
        1 - _ratio_extreme(fixed, free, smallest=False),
        1 - _ratio_extreme(fixed, free, smallest=True),
    )


@dataclass(frozen=True)
class DirectionalSensitivity:
    """Bounds for the headline figures; the climate figures use only the forecasts that have that reference, settled or excluded."""

    settled: int
    excluded: int
    brier: Bounds
    log_loss: Bounds
    brier_vs_half: Bounds
    log_vs_half: Bounds
    brier_vs_climate: Bounds | None
    log_vs_climate: Bounds | None


def _figures(
    kind: Callable[[Decimal, int], Decimal], settled: Sequence[Directional], excluded: Sequence[ExcludedDirectional]
) -> tuple[Bounds, Bounds, Bounds | None]:
    """Bounds on one score's mean, on its skill against one half and on its skill against the climatological rate."""
    both = (0, 1)
    mean = _mean_bounds([kind(r.p, r.outcome) for r in settled], [[kind(e.p, o) for o in both] for e in excluded])
    half = _skill_bounds(
        [(kind(r.p, r.outcome), kind(HALF, r.outcome)) for r in settled], [[(kind(e.p, o), kind(HALF, o)) for o in both] for e in excluded]
    )
    own = [(r, r.climate) for r in settled if r.climate is not None]
    others = [(e, e.climate) for e in excluded if e.climate is not None]
    climate = (
        _skill_bounds(
            [(kind(r.p, r.outcome), kind(q, r.outcome)) for r, q in own],
            [[(kind(e.p, o), kind(q, o)) for o in both] for e, q in others],
        )
        if own
        else None
    )
    return mean, half, climate


@_exact
def directional_sensitivity(settled: Sequence[Directional], excluded: Sequence[ExcludedDirectional]) -> DirectionalSensitivity:
    if not settled:
        raise ScoringError("nothing settled to put bounds on")
    brier_mean, brier_half, brier_climate = _figures(brier, settled, excluded)
    loss_mean, loss_half, loss_climate = _figures(log_loss, settled, excluded)
    return DirectionalSensitivity(len(settled), len(excluded), brier_mean, loss_mean, brier_half, loss_half, brier_climate, loss_climate)


def _possible(excluded: ExcludedBarrier) -> tuple[str, ...]:
    """The ends an excluded barrier forecast could have had. One that is AMBIGUOUS touched both levels in one minute (contract section
    3), so it reached its target or its stop first and never timed out; for any other reason nothing is known of its end."""
    return AMBIGUOUS_OUTCOMES if excluded.reason == "AMBIGUOUS" else BARRIER_CLASSES


@_exact
def barrier_sensitivity(settled: Sequence[Barrier], excluded: Sequence[ExcludedBarrier]) -> Bounds:
    if not settled:
        raise ScoringError("nothing settled to put bounds on")
    return _mean_bounds(
        [brier3(r.probabilities, r.outcome) for r in settled],
        [[brier3(e.probabilities, name) for name in _possible(e)] for e in excluded],
    )


# --- shadow trades ----------------------------------------------------------------------------------------------------------------------
SHADOW_STATES: Final = ("SETTLED", "NO_REFERENCE", "UNSETTLEABLE", "PENDING")


@dataclass(frozen=True)
class ShadowTrade:
    """What a voluntary abstain would have done under the reference policy: a net return, or why there is none."""

    abstain_id: str
    state: str
    net_return: Decimal | None = None

    def __post_init__(self) -> None:
        _label(self.abstain_id, "an id")
        if self.state not in SHADOW_STATES:
            raise ScoringError("a shadow trade is SETTLED, NO_REFERENCE, UNSETTLEABLE or PENDING")
        if (self.state == "SETTLED") != (self.net_return is not None) or (self.net_return is not None and not self.net_return.is_finite()):
            raise ScoringError("only a settled shadow trade has a return, and it is finite")


@dataclass(frozen=True)
class ShadowReport:
    """Hypothetical, never merged into an arm's own result. The denominator is every voluntary abstain."""

    abstains: int
    settled: int
    no_reference: int
    unsettleable: int
    pending: int
    share_settled: Decimal | None
    mean_return: Decimal | None
    hypothetical: bool = True


@_exact
def shadow_report(trades: Sequence[ShadowTrade]) -> ShadowReport:
    counted = Counter(t.state for t in trades)
    returns = [t.net_return for t in trades if t.net_return is not None]
    return ShadowReport(
        len(trades),
        counted["SETTLED"],
        counted["NO_REFERENCE"],
        counted["UNSETTLEABLE"],
        counted["PENDING"],
        Decimal(counted["SETTLED"]) / len(trades) if trades else None,
        _mean(returns) if returns else None,
    )
