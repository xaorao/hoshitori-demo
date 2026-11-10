"""Checks of the bar coverage measure (``hoshitori.bar_coverage``), runnable against a mutant."""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hoshitori.venue_data import Candle, CandleSeries

T0 = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)


def series(volumes: list[str | None], missing: int = 0) -> CandleSeries:
    candles = tuple(
        Candle(T0 + timedelta(minutes=n), T0 + timedelta(minutes=n + 1), "1", "1", "1", "1", volume=v) for n, v in enumerate(volumes)
    )
    gaps = tuple(T0 + timedelta(minutes=len(volumes) + n) for n in range(missing))
    end = T0 + timedelta(minutes=len(volumes) + missing)
    return CandleSeries("v", "X", 60, T0, end, end, candles, 0, gaps, ())


class Case:
    def __init__(self) -> None:
        self.bad: list[str] = []

    def eq(self, label: str, got: Any, want: Any) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def failures(mod: types.ModuleType) -> list[str]:
    c = Case()
    c.eq("the gate is a prior of 0.98", mod.MIN_PRESENT, Decimal("0.98"))
    full = mod.measure(series(["3"] * 60))
    c.eq(
        "every minute present",
        (full.venue, full.instrument, full.closed_slots, full.present, full.padded, full.unknown_volume),
        ("v", "X", 60, 60, 0, 0),
    )
    c.eq("and its shares", (full.present_share, full.padded_share, full.can_settle), (Decimal(1), Decimal(0), True))
    thin = mod.measure(series(["1"] * 9, missing=51))
    c.eq("nine minutes of sixty", (thin.closed_slots, thin.present, thin.present_share, thin.can_settle), (60, 9, Decimal("0.15"), False))
    pad = mod.measure(series(["0", "0.00000000", "2", "0"] + ["1"] * 16))
    c.eq(
        "padded bars are counted and still answer",
        (pad.present, pad.padded, pad.padded_share, pad.can_settle),
        (20, 3, Decimal("0.15"), True),
    )
    odd = mod.measure(series([None, "x", "5"]))
    c.eq("a bar with no readable volume is neither padded nor trusted as traded", (odd.padded, odd.unknown_volume), (0, 2))
    edge = mod.measure(series(["1"] * 49, missing=1))
    c.eq("49 of 50 is exactly the gate", (edge.present_share, edge.can_settle), (Decimal("0.98"), True))
    under = mod.measure(series(["1"] * 48, missing=2))
    c.eq("48 of 50 is below it", (under.present_share, under.can_settle), (Decimal("0.96"), False))
    none = mod.measure(series([]))
    c.eq(
        "no closed slot is no evidence", (none.closed_slots, none.present_share, none.padded_share, none.can_settle), (0, None, None, False)
    )
    c.eq("Coverage is frozen", mod.Coverage.__dataclass_params__.frozen, True)
    return c.bad
