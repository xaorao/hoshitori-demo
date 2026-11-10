"""The scale and the market beta of the falsification tests are fitted on minutes before the announcement and never on later ones (plan F-01, leakage check)."""

from __future__ import annotations

import random

from hoshitori import falsify

TA = 570
H = 240


def _series(seed: int) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    market = [rng.gauss(0.0, 0.0006) for _ in range(TA + H + 10)]
    token = [1.2 * m + rng.gauss(0.0, 0.0008) for m in market]
    return token, market


def _plant_future(token: list[float], market: list[float], size: float) -> tuple[list[float], list[float]]:
    """The same series with every minute from the announcement on replaced by a large move."""
    return (
        token[:TA] + [size] * (len(token) - TA),
        market[:TA] + [-size] * (len(market) - TA),
    )


def test_market_fit_ignores_every_minute_from_the_announcement_on() -> None:
    params = falsify.MarketParams()
    for seed in range(5):
        token, market = _series(seed)
        base = falsify.market_scores(token, market, TA, H, params)
        future_token, future_market = _plant_future(token, market, 0.05)
        moved = falsify.market_scores(future_token, future_market, TA, H, params)
        assert (moved.beta, moved.sigma_residual, moved.sigma_raw) == (base.beta, base.sigma_residual, base.sigma_raw)


def test_onset_scale_ignores_every_minute_from_the_announcement_on() -> None:
    params = falsify.OnsetParams()
    for seed in range(5):
        token, market = _series(seed)
        base = falsify.onset_scores(token, TA, H, params)
        future_token, _ = _plant_future(token, market, 0.05)
        moved = falsify.onset_scores(future_token, TA, H, params)
        assert moved.sigma == base.sigma


def test_a_planted_future_move_changes_the_post_score_only() -> None:
    token, market = _series(1)
    future_token, future_market = _plant_future(token, market, 0.05)
    base = falsify.market_scores(token, market, TA, H, falsify.MarketParams())
    moved = falsify.market_scores(future_token, future_market, TA, H, falsify.MarketParams())
    assert moved.beta == base.beta
    assert moved.z_residual != base.z_residual
