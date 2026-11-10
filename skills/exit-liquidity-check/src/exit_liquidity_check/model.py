"""The exit-cost model, and the assumptions it rests on.

Adapted from ``hanko/skills/exit_liquidity/model.py`` in IamHarrie-Labs/hanko (MIT, Copyright (c) 2026 Harrie; see NOTICE.md in the
repository that holds this skill). The model, its names and its validity limit follow that file; the functions are rewritten for this
package's conventions and are checked here against hand-computed values and properties (``tests/model_checks.py``).

Nobody hands out order books. What is available is pool liquidity and 24h volume, which is enough to *model* an exit but not to *observe*
one. Every figure from this module is therefore labelled modelled, and the model's assumptions travel with the answer.

THE MODEL (cpmm_v1). A constant-product pool, x * y = k. Selling dx of the base token returns dy = y*dx/(x+dx), so against the mid price
the shortfall is ``slippage = dx / (x + dx) = f / (1 + f)`` with ``f = dx / x``. Reported pool liquidity is taken to be total value
locked, twice the value of either side of a balanced pool, so selling S dollars gives ``f = 2S / liquidity_usd``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

MODEL_ID: Final = "cpmm_v1"

# Past this share of the pool a constant-product curve stops being a defensible picture of a real venue: routers split, other pools absorb
# flow and market makers step away. The answer then says the figure is a floor on the cost rather than extrapolating a number.
VALIDITY_LIMIT: Final = 0.25

ASSUMPTIONS: Final = (
    "the pool behaves as constant-product (x*y=k)",
    "reported liquidity is total value locked, twice one side",
    "all liquidity is reachable in a single route",
    "no fees, no MEV, no price movement during the exit",
    "mid price at the time of the reading",
)


def slippage_for(size_usd: float, liquidity_usd: float) -> float:
    """The share of the notional lost to price impact, 0 to 1."""
    if liquidity_usd <= 0 or size_usd <= 0:
        return 0.0
    f = 2.0 * size_usd / liquidity_usd
    return f / (1.0 + f)


def size_for_slippage(target: float, liquidity_usd: float) -> float:
    """The largest notional whose modelled slippage stays at or under ``target`` (a share, 0 to 1)."""
    if liquidity_usd <= 0 or target <= 0:
        return 0.0
    if target >= 1.0:
        return math.inf
    return target / (1.0 - target) * liquidity_usd / 2.0


def pool_fraction(size_usd: float, liquidity_usd: float) -> float:
    """The share of the pool the exit represents (``f`` above); it decides whether the model still holds."""
    if liquidity_usd <= 0:
        return math.inf
    return 2.0 * size_usd / liquidity_usd


def hours_to_exit(size_usd: float, volume_24h_usd: float, participation: float) -> float | None:
    """Hours to unwind while staying at or under ``participation`` (a share) of daily volume; none without volume."""
    if volume_24h_usd <= 0 or participation <= 0 or size_usd <= 0:
        return None
    return 24.0 * size_usd / (volume_24h_usd * participation)


def size_for_hours(hours: float, volume_24h_usd: float, participation: float) -> float | None:
    """The largest notional that unwinds within ``hours`` at the same cap: what the market's own flow can absorb.

    It needs no pool depth, so it still separates a deep market from a thin one when the depth figure is missing."""
    if volume_24h_usd <= 0 or participation <= 0 or hours <= 0:
        return None
    return hours * volume_24h_usd * participation / 24.0


def turnover(volume_24h_usd: float, market_cap_usd: float) -> float | None:
    """The share of the token's whole value that changes hands in a day; none without a market cap."""
    if market_cap_usd <= 0 or volume_24h_usd < 0:
        return None
    return volume_24h_usd / market_cap_usd


@dataclass(frozen=True, slots=True)
class Estimate:
    size_usd: float
    slippage: float
    cost_usd: float
    proceeds_usd: float
    pool_fraction: float
    within_model: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "size_usd": round(self.size_usd, 2),
            "slippage_pct": round(self.slippage * 100, 4),
            "cost_usd": round(self.cost_usd, 2),
            "proceeds_usd": round(self.proceeds_usd, 2),
            "pool_fraction_pct": round(self.pool_fraction * 100, 3),
            "basis": "modelled",
            "model": MODEL_ID,
            "within_model_validity": self.within_model,
        }


def estimate(size_usd: float, liquidity_usd: float) -> Estimate:
    slip = slippage_for(size_usd, liquidity_usd)
    fraction = pool_fraction(size_usd, liquidity_usd)
    cost = size_usd * slip
    return Estimate(size_usd, slip, cost, size_usd - cost, fraction, fraction <= VALIDITY_LIMIT)


LADDER_FRACTIONS: Final = (0.005, 0.01, 0.025, 0.05, 0.10, 0.25)


def default_ladder(liquidity_usd: float) -> tuple[float, ...]:
    """Sizes scaled to the pool (a fixed dollar ladder says nothing about both a $50k pool and a $50m one)."""
    if liquidity_usd <= 0:
        return ()
    return tuple(round(liquidity_usd * share / 2.0, 2) for share in LADDER_FRACTIONS)


def curve(liquidity_usd: float, sizes: tuple[float, ...]) -> tuple[Estimate, ...]:
    return tuple(estimate(size, liquidity_usd) for size in sorted(sizes))
