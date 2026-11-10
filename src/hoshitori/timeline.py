"""The clocks of a forecast as functions on whole milliseconds: when it may start, where its reference price is, when it ends,
when a practice trade may fill, and how long evidence may wait for the origin.

Contract sections 2, 4 and 25 are the rules; ``SIMULATOR_SPEC`` section 2 is the form. Every instant is an integer number of
milliseconds since the epoch (UTC), every duration an integer number of milliseconds, and nothing here reads a clock or a
file, so every answer can be recomputed from what was stored. A one-minute candle that opens at ``o`` closes at ``o + 60 000``
(an exclusive end); the oracle's own ``closeTime`` is never the boundary.

The three clocks never merge: ``authorized_at`` (the independent commitment plus the clock uncertainty), the forecast origin
``T0`` (the close of the first candle that closes strictly after it) and the earliest fill (never before either). Execution
therefore never precedes the origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

MINUTE_MS: Final = 60_000
HOUR_MS: Final = 3_600_000
HORIZON_1H: Final = HOUR_MS
HORIZON_4H: Final = 4 * HOUR_MS
HORIZON_24H: Final = 24 * HOUR_MS
DEFAULT_HORIZONS: Final = (HORIZON_1H, HORIZON_4H, HORIZON_24H)
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

TOKEN_BEFORE_MS: Final = 1_000  # a token counts from this long before the signed commit time ...
TOKEN_AFTER_MS: Final = 60_000  # ... to this long after it
ATTESTATION_RETRY_MS: Final = 30_000  # and the attestation phase closes this long after the window
LEAGUE_GAP_CAP_MS: Final = 15 * MINUTE_MS
LEAGUE_GAP_DIVISOR: Final = 10
ARENA_GAP_DIVISOR: Final = 24
CUTOFF_MARGIN_MS: Final = 120_000
DISCOVERY_TO_SNAPSHOT_MS: Final = 10 * MINUTE_MS


class TimelineError(ValueError):
    """An instant or a duration that cannot be a time on these clocks."""


@dataclass(frozen=True)
class ClockParameters:
    """The pre-registered constants (priors until the protocol fixes them)."""

    uncertainty_ms: int = 5_000  # u: the assumed clock uncertainty of a commitment
    latency_ms: int = 15_000  # L: the practice trade's delay after authorisation

    def __post_init__(self) -> None:
        if self.uncertainty_ms < 0 or self.latency_ms < 0:
            raise TimelineError("the clock constants are not negative")


def to_ms(moment: datetime) -> int:
    """An instant as whole milliseconds; a naive instant or one with a fraction of a millisecond is refused."""
    if moment.utcoffset() is None:
        raise TimelineError("an instant has a zone")
    delta = moment.astimezone(UTC) - EPOCH
    if delta.microseconds % 1000 != 0:
        raise TimelineError("an instant is a whole number of milliseconds")
    return _instant((delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000, "the instant")


def from_ms(ms: int) -> datetime:
    _instant(ms, "the instant")
    return EPOCH + timedelta(milliseconds=ms)


def _instant(value: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TimelineError(f"{what} is a whole number of milliseconds, not before the epoch")
    return value


def _duration(value: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TimelineError(f"{what} is a whole, non-negative number of milliseconds")
    return value


def horizon_ok(horizon_ms: int) -> int:
    """A horizon the contract can settle: whole minutes, at least an hour (one-minute candles must be no longer than a sixtieth of it)."""
    if isinstance(horizon_ms, bool) or not isinstance(horizon_ms, int) or horizon_ms < HOUR_MS or horizon_ms % MINUTE_MS != 0:
        raise TimelineError("a horizon is a whole number of minutes, at least one hour")
    return horizon_ms


# --- the origin -----------------------------------------------------------------------------------
def eligible_after(attested_at_ms: int, uncertainty_ms: int, spread_ms: int) -> int:
    """The earliest instant a market observation may count: the attestation, plus the uncertainty, plus the spread between the tokens."""
    return (
        _instant(attested_at_ms, "the attestation time") + _duration(uncertainty_ms, "the uncertainty") + _duration(spread_ms, "the spread")
    )


def origin(eligible_after_ms: int) -> int:
    """T0: the close of the first one-minute candle that closes strictly after ``eligible_after``. On a boundary, the next one."""
    return (_instant(eligible_after_ms, "eligible_after") // MINUTE_MS + 1) * MINUTE_MS


def reference_candle_open(origin_ms: int) -> int:
    """The candle whose close is the reference price ``P0``: the one that closes at T0."""
    return _origin(origin_ms) - MINUTE_MS


def outcome_close(origin_ms: int, horizon_ms: int) -> int:
    """The instant of the outcome price: the close of the candle that closes at ``T0 + horizon``."""
    return _origin(origin_ms) + horizon_ok(horizon_ms)


def outcome_candle_open(origin_ms: int, horizon_ms: int) -> int:
    return outcome_close(origin_ms, horizon_ms) - MINUTE_MS


def _origin(origin_ms: int) -> int:
    if _instant(origin_ms, "the origin") % MINUTE_MS != 0 or origin_ms < MINUTE_MS:
        raise TimelineError("the origin is a whole minute after the epoch")
    return origin_ms


# --- the fill -------------------------------------------------------------------------------------
def earliest_fill(authorized_at_ms: int, latency_ms: int, origin_ms: int) -> int:
    """No fill before authorisation plus latency, and none before the origin."""
    return max(_instant(authorized_at_ms, "the authorisation time") + _duration(latency_ms, "the latency"), _origin(origin_ms))


def fill_candle_open(earliest_fill_ms: int) -> int:
    """The first one-minute candle that opens at or after the earliest fill. Never the candle that closes at T0, which opened a
    minute earlier."""
    return -(-_instant(earliest_fill_ms, "the earliest fill") // MINUTE_MS) * MINUTE_MS


# --- the attestation ------------------------------------------------------------------------------
def token_window(committed_at_ms: int) -> tuple[int, int]:
    """The generation times that count for a commitment signed at ``committed_at``, both ends included."""
    committed = _instant(committed_at_ms, "the commit time")
    return max(committed - TOKEN_BEFORE_MS, 0), committed + TOKEN_AFTER_MS


def token_counts(gen_time_ms: int, committed_at_ms: int) -> bool:
    low, high = token_window(committed_at_ms)
    return low <= _instant(gen_time_ms, "the generation time") <= high


def attestation_close(committed_at_ms: int) -> int:
    """The attestation phase ends here at the latest; the origin is set only after it (a late token changes neither the origin nor
    the level)."""
    return _instant(committed_at_ms, "the commit time") + TOKEN_AFTER_MS + ATTESTATION_RETRY_MS


# --- the gap between the evidence and the origin --------------------------------------------------
def gap_bound(horizon_ms: int, *, arena: bool = False) -> int:
    """How long the frozen evidence may wait for the origin. League: the smaller of 15 minutes and a tenth of the horizon. Arena: a
    24th."""
    horizon = horizon_ok(horizon_ms)
    if arena:
        return horizon // ARENA_GAP_DIVISOR
    return min(LEAGUE_GAP_CAP_MS, horizon // LEAGUE_GAP_DIVISOR)


def gap_ms(snapshot_at_ms: int, origin_ms: int) -> int:
    if _origin(origin_ms) < _instant(snapshot_at_ms, "the snapshot time"):
        raise TimelineError("the origin is not before the snapshot")
    return origin_ms - snapshot_at_ms


def void_gap(snapshot_at_ms: int, origin_ms: int, horizon_ms: int, *, arena: bool = False) -> bool:
    """A horizon whose bound is exceeded is void for that horizon only."""
    return gap_ms(snapshot_at_ms, origin_ms) > gap_bound(horizon_ms, arena=arena)


def decision_cutoff(p95_latency_ms: int, horizon_ms: int, *, arena: bool = False) -> int | None:
    """The time an arm has after the snapshot: one and a half times its 95th-percentile latency, but never so long that the
    attestation and the next     candle close could not fit inside the gap bound. ``None`` if the arm's latency does not fit
    the horizon at all (it is not admitted there)."""
    room = (
        gap_bound(horizon_ms, arena=arena) - CUTOFF_MARGIN_MS
    )  # always positive: a horizon is at least an hour, so a bound at least 150 s
    latency = _duration(p95_latency_ms, "the latency")
    if latency > room:
        return None
    return min(-(-latency * 3 // 2), room)


def cycle_order_ok(discovery_at_ms: int, sealed_at_ms: int, snapshot_at_ms: int) -> bool:
    """``discovery_at <= manifest_sealed_at <= snapshot_at`` and the snapshot not more than ten minutes after the discovery."""
    discovery = _instant(discovery_at_ms, "the discovery time")
    sealed = _instant(sealed_at_ms, "the sealing time")
    snapshot = _instant(snapshot_at_ms, "the snapshot time")
    return discovery <= sealed <= snapshot and snapshot - discovery <= DISCOVERY_TO_SNAPSHOT_MS
