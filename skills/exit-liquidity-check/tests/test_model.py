"""The exit-cost model against hand-computed values and its properties."""

from __future__ import annotations

import math

import pytest

from exit_liquidity_check import model


def test_slippage_hand_computed() -> None:
    # liquidity 1,000,000 (TVL): selling 10,000 is f = 2*10000/1e6 = 0.02, slippage 0.02/1.02
    assert model.slippage_for(10_000, 1_000_000) == pytest.approx(0.02 / 1.02)
    assert model.slippage_for(500_000, 1_000_000) == pytest.approx(0.5)  # f = 1, slippage 1/2
    assert model.slippage_for(0, 1_000_000) == 0.0
    assert model.slippage_for(10_000, 0) == 0.0


def test_size_for_slippage_inverts_slippage_for() -> None:
    for liquidity in (50_000.0, 1_234_567.0, 9e9):
        for target in (0.001, 0.01, 0.03, 0.2):
            size = model.size_for_slippage(target, liquidity)
            assert model.slippage_for(size, liquidity) == pytest.approx(target, rel=1e-9)
    assert model.size_for_slippage(0.03, 1_000_000) == pytest.approx(0.03 / 0.97 * 500_000)
    assert model.size_for_slippage(1.0, 1_000_000) == math.inf
    assert model.size_for_slippage(0.0, 1_000_000) == 0.0
    assert model.size_for_slippage(0.03, 0) == 0.0


def test_cost_grows_with_size_and_falls_with_depth() -> None:
    sizes = [1_000.0 * 3**n for n in range(10)]
    costs = [model.slippage_for(s, 1_000_000) for s in sizes]
    assert costs == sorted(costs) and len(set(costs)) == len(costs)
    assert all(0.0 <= c < 1.0 for c in costs)
    deeper = [model.slippage_for(s, 2_000_000) for s in sizes]
    assert all(d < c for d, c in zip(deeper, costs, strict=True))


def test_validity_limit_is_a_quarter_of_the_pool() -> None:
    inside = model.estimate(125_000, 1_000_000)  # f = 0.25 exactly
    outside = model.estimate(125_001, 1_000_000)
    assert inside.within_model and not outside.within_model
    assert inside.cost_usd + inside.proceeds_usd == pytest.approx(125_000)


def test_hours_and_capacity() -> None:
    assert model.hours_to_exit(10_000, 1_000_000, 0.10) == pytest.approx(2.4)  # 24 * 1e4 / (1e6 * 0.1)
    assert model.size_for_hours(2.4, 1_000_000, 0.10) == pytest.approx(10_000)
    for volume, share in ((0, 0.1), (1e6, 0), (-1, 0.1)):
        assert model.hours_to_exit(10_000, volume, share) is None
        assert model.size_for_hours(1.0, volume, share) is None
    assert model.hours_to_exit(0, 1e6, 0.1) is None


def test_turnover() -> None:
    assert model.turnover(46, 100) == pytest.approx(0.46)
    assert model.turnover(1, 0) is None
    assert model.turnover(-1, 100) is None


def test_ladder_is_scaled_to_the_pool() -> None:
    ladder = model.default_ladder(1_000_000)
    assert ladder == (2_500.0, 5_000.0, 12_500.0, 25_000.0, 50_000.0, 125_000.0)
    assert model.default_ladder(0) == ()
    steps = model.curve(1_000_000, ladder)
    assert [e.within_model for e in steps] == [True] * 6  # the last rung is exactly at the limit
    assert model.curve(1_000_000, (9.0, 1.0, 5.0))[0].size_usd == 1.0  # sorted by size
