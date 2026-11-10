"""Checks of ``exit_liquidity_check.model`` against hand-computed values, runnable against a mutant."""

from __future__ import annotations

import math
import types

from tests.checks_support import Case


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("model id", mod.MODEL_ID, "cpmm_v1")
    c.eq("validity limit", mod.VALIDITY_LIMIT, 0.25)
    c.eq(
        "assumptions",
        mod.ASSUMPTIONS,
        (
            "the pool behaves as constant-product (x*y=k)",
            "reported liquidity is total value locked, twice one side",
            "all liquidity is reachable in a single route",
            "no fees, no MEV, no price movement during the exit",
            "mid price at the time of the reading",
        ),
    )
    c.eq("ladder fractions", mod.LADDER_FRACTIONS, (0.005, 0.01, 0.025, 0.05, 0.10, 0.25))

    # slippage_for: f = 2S/L, slippage f/(1+f)
    c.near("slip 10k/1m", mod.slippage_for(10_000, 1_000_000), 0.02 / 1.02)
    c.near("slip f=1", mod.slippage_for(500_000, 1_000_000), 0.5)
    c.near("slip f=3", mod.slippage_for(1_500_000, 1_000_000), 0.75)
    c.eq("slip zero size", mod.slippage_for(0, 1_000_000), 0.0)
    c.eq("slip negative size", mod.slippage_for(-5, 1_000_000), 0.0)
    c.eq("slip zero liquidity", mod.slippage_for(10, 0), 0.0)
    c.eq("slip negative liquidity", mod.slippage_for(10, -1_000), 0.0)
    c.near("slip tiny positive", mod.slippage_for(1, 2), 0.5)

    c.near("slip fractional liquidity", mod.slippage_for(0.5, 0.5), 2.0 / 3.0)
    c.near("slip fractional size", mod.slippage_for(0.5, 1.0), 0.5)

    # size_for_slippage: target/(1-target) * L/2
    c.near("size 3%", mod.size_for_slippage(0.03, 1_000_000), 0.03 / 0.97 * 500_000)
    c.near("size 50%", mod.size_for_slippage(0.5, 1_000_000), 500_000)
    c.eq("size target 1", mod.size_for_slippage(1.0, 1_000_000), math.inf)
    c.eq("size target above 1", mod.size_for_slippage(2.0, 1_000_000), math.inf)
    c.eq("size target 0", mod.size_for_slippage(0.0, 1_000_000), 0.0)
    c.eq("size target negative", mod.size_for_slippage(-0.1, 1_000_000), 0.0)
    c.eq("size zero liquidity", mod.size_for_slippage(0.03, 0), 0.0)
    c.eq("size negative liquidity", mod.size_for_slippage(0.03, -1), 0.0)
    c.eq("size zero liquidity target 1", mod.size_for_slippage(1.0, 0), 0.0)
    c.near("size just under 1", mod.size_for_slippage(0.999, 2.0), 999.0)

    c.near("size fractional liquidity", mod.size_for_slippage(0.5, 0.5), 0.25)
    c.near("size fractional target", mod.size_for_slippage(0.5, 1000.0), 500.0)

    # pool_fraction
    c.near("fraction fractional liquidity", mod.pool_fraction(1, 0.5), 4.0)
    c.near("fraction", mod.pool_fraction(125_000, 1_000_000), 0.25)
    c.eq("fraction zero liquidity", mod.pool_fraction(1, 0), math.inf)
    c.eq("fraction negative liquidity", mod.pool_fraction(1, -3), math.inf)
    c.eq("fraction zero size", mod.pool_fraction(0, 100), 0.0)

    # hours_to_exit
    c.near("hours", mod.hours_to_exit(10_000, 1_000_000, 0.10), 2.4)
    c.near("hours 2", mod.hours_to_exit(5, 10, 1.0), 12.0)
    c.near("hours fractional volume", mod.hours_to_exit(1, 0.5, 1.0), 48.0)
    c.near("hours fractional participation", mod.hours_to_exit(1, 24.0, 0.5), 2.0)
    c.near("hours fractional size", mod.hours_to_exit(0.5, 24.0, 1.0), 0.5)
    for label, args in (
        ("zero volume", (10_000, 0, 0.1)),
        ("negative volume", (10_000, -1, 0.1)),
        ("zero participation", (10_000, 1e6, 0)),
        ("negative participation", (10_000, 1e6, -0.1)),
        ("zero size", (0, 1e6, 0.1)),
        ("negative size", (-1, 1e6, 0.1)),
    ):
        c.eq(f"hours {label}", mod.hours_to_exit(*args), None)

    # size_for_hours
    c.near("capacity", mod.size_for_hours(2.4, 1_000_000, 0.10), 10_000)
    c.near("capacity 1h", mod.size_for_hours(1.0, 2_400, 0.5), 50.0)
    c.near("capacity fractional volume", mod.size_for_hours(24.0, 0.5, 1.0), 0.5)
    c.near("capacity fractional participation", mod.size_for_hours(24.0, 1.0, 0.5), 0.5)
    c.near("capacity fractional hours", mod.size_for_hours(0.5, 48.0, 1.0), 1.0)
    for label, args in (
        ("zero volume", (1.0, 0, 0.1)),
        ("negative volume", (1.0, -1, 0.1)),
        ("zero participation", (1.0, 1e6, 0)),
        ("negative participation", (1.0, 1e6, -0.1)),
        ("zero hours", (0, 1e6, 0.1)),
        ("negative hours", (-1, 1e6, 0.1)),
    ):
        c.eq(f"capacity {label}", mod.size_for_hours(*args), None)

    # turnover: zero volume is a real zero, a missing market cap is none
    c.near("turnover", mod.turnover(46, 100), 0.46)
    c.eq("turnover zero volume", mod.turnover(0, 100), 0.0)
    c.near("turnover fractional cap", mod.turnover(1, 0.5), 2.0)
    c.near("turnover fractional volume", mod.turnover(0.5, 1), 0.5)
    c.eq("turnover zero cap", mod.turnover(1, 0), None)
    c.eq("turnover negative cap", mod.turnover(1, -100), None)
    c.eq("turnover negative volume", mod.turnover(-1, 100), None)

    # estimate and its as_dict
    e = mod.estimate(125_000, 1_000_000)
    c.eq("estimate size", e.size_usd, 125_000)
    c.near("estimate slippage", e.slippage, 0.25 / 1.25)
    c.near("estimate cost", e.cost_usd, 125_000 * 0.2)
    c.near("estimate proceeds", e.proceeds_usd, 125_000 * 0.8)
    c.near("estimate fraction", e.pool_fraction, 0.25)
    c.eq("estimate at the limit is inside", e.within_model, True)
    c.eq("estimate past the limit is outside", mod.estimate(125_001, 1_000_000).within_model, False)
    c.eq("estimate just inside", mod.estimate(124_999, 1_000_000).within_model, True)
    z = mod.estimate(1_000, 0)
    c.eq(
        "estimate zero depth", (z.slippage, z.cost_usd, z.proceeds_usd, z.pool_fraction, z.within_model), (0.0, 0.0, 1_000, math.inf, False)
    )
    c.eq(
        "as_dict",
        mod.estimate(10_000, 1_000_000).as_dict(),
        {
            "size_usd": 10_000.0,
            "slippage_pct": round(0.02 / 1.02 * 100, 4),
            "cost_usd": round(10_000 * 0.02 / 1.02, 2),
            "proceeds_usd": round(10_000 - 10_000 * 0.02 / 1.02, 2),
            "pool_fraction_pct": 2.0,
            "basis": "modelled",
            "model": "cpmm_v1",
            "within_model_validity": True,
        },
    )
    d = mod.estimate(1234.5678, 987_654.321).as_dict()
    c.eq(
        "as_dict rounding",
        (d["size_usd"], d["slippage_pct"], d["cost_usd"], d["proceeds_usd"], d["pool_fraction_pct"]),
        (1234.57, 0.2494, 3.08, 1231.49, 0.25),
    )

    d3 = mod.estimate(12_345, 7_000_000).as_dict()
    c.eq("as_dict fraction keeps three decimals", d3["pool_fraction_pct"], 0.353)
    try:
        e.size_usd = 1.0  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        c.failed.append("Estimate must be frozen")
    c.eq("Estimate has no __dict__ (slots)", hasattr(e, "__dict__"), False)

    # ladder and curve
    c.eq("ladder fractional liquidity", mod.default_ladder(0.5), (0.0, 0.0, 0.01, 0.01, 0.03, 0.06))
    c.eq("ladder empty", mod.default_ladder(0), ())
    c.eq("ladder empty negative", mod.default_ladder(-5), ())
    c.eq("ladder", mod.default_ladder(1_000_000), (2_500.0, 5_000.0, 12_500.0, 25_000.0, 50_000.0, 125_000.0))
    c.eq("ladder rounds", mod.default_ladder(1_234.5678), (3.09, 6.17, 15.43, 30.86, 61.73, 154.32))
    curve = mod.curve(1_000_000, (50_000.0, 10_000.0, 30_000.0))
    c.eq("curve sorted", tuple(x.size_usd for x in curve), (10_000.0, 30_000.0, 50_000.0))
    c.eq("curve is a tuple", type(curve), tuple)
    c.near("curve value", curve[0].slippage, 0.02 / 1.02)
    c.eq("curve empty", mod.curve(1_000_000, ()), ())
    return c.failed
