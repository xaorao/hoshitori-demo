"""The frozen first-touch frequencies of the rules arm's barrier forecast (``RULES_ARM_SPEC`` section 5b).

From the hourly and one-minute bars of a universe: an origin is an hour boundary, ``P0`` is the close of the minute bar that
opens one minute before it, the barriers lie ``k x ATR14`` above and below ``P0`` (ATR14 from the hourly bars that closed by
the origin, by the function the live arm uses), and the first minute of the horizon that touches a level decides. A minute
that touches both is ambiguous and is counted apart: no finer data exists here. A window with a missing minute before the
first touch (the settlement could not decide either), or with no ATR, is skipped and counted. The next origin of an
instrument is at least one horizon later, so windows never overlap. The result is a canonical file with its hash; it is
exploratory until a protocol pins it.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Final

from hoshitori import canonical
from hoshitori.features import ATR_BARS, atr_over
from hoshitori.rules_arm import HORIZONS, MAX_INSTANT_MS, MIN_CELL, P_CEIL, P_FLOOR, RulesError
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339
from hoshitori.venue_data import Candle

RULE_VERSION: Final = "1"
K_TARGET: Final = Decimal("1.5")
K_STOP: Final = Decimal("1.5")
_HOUR: Final = timedelta(hours=1)
_MINUTE: Final = timedelta(minutes=1)
CLASSES: Final = ("target", "stop", "timeout", "ambiguous")


@dataclass(frozen=True)
class Cell:
    target: int  # the target was touched first
    stop: int
    timeout: int  # neither within the horizon
    ambiguous: int  # one minute touched both: counted apart, not in n

    @property
    def n(self) -> int:
        return self.target + self.stop + self.timeout


@dataclass(frozen=True)
class BarrierRates:
    cells: tuple[tuple[int, Cell], ...]  # (horizon in hours, cell) for every horizon, in order
    venue: str
    quote_class: str
    instruments: tuple[str, ...]
    period_start_ms: int
    period_end_ms: int
    skipped: int  # origins left out for a missing bar or no ATR

    def __post_init__(self) -> None:
        if [h for h, _ in self.cells] != list(HORIZONS):
            raise RulesError("a barrier mapping has a cell for every horizon, in order")
        for _, cell in self.cells:
            if min(cell.target, cell.stop, cell.timeout, cell.ambiguous, self.skipped) < 0:
                raise RulesError("a cell counts windows and nothing is negative")
        for what, value in (("the period start", self.period_start_ms), ("the period end", self.period_end_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
                raise RulesError(f"{what} is a whole number of milliseconds up to the end of the year 9999")
        if self.period_end_ms < self.period_start_ms:
            raise RulesError("the period ends no earlier than it starts")
        if not self.venue or not self.quote_class or list(self.instruments) != sorted(set(self.instruments)):
            raise RulesError("a mapping names its venue, its quote class and its instruments, sorted and once each")

    def cell(self, hours: int) -> Cell:
        return dict(self.cells)[hours]

    def p(self, hours: int) -> tuple[Decimal, Decimal, Decimal] | None:
        """``(p_target, p_stop, p_timeout)`` of the settled windows, summing to exactly one, or ``None`` below ``MIN_CELL`` windows."""
        cell = self.cell(hours)
        if cell.n < MIN_CELL:
            return None
        target, stop = _share(cell.target, cell.n), _share(cell.stop, cell.n)
        return target, stop, Decimal(1) - target - stop

    def body(self) -> dict[str, canonical.JsonValue]:
        return {
            "rule_version": RULE_VERSION,
            "min_cell": MIN_CELL,
            "k_target": format(K_TARGET, "f"),
            "k_stop": format(K_STOP, "f"),
            "venue": self.venue,
            "quote_class": self.quote_class,
            "instruments": list(self.instruments),
            "period_start": format_rfc3339_ms(from_ms(self.period_start_ms)),
            "period_end": format_rfc3339_ms(from_ms(self.period_end_ms)),
            "skipped": self.skipped,
            "cells": {str(h): {"target": c.target, "stop": c.stop, "timeout": c.timeout, "ambiguous": c.ambiguous} for h, c in self.cells},
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical.dumps(self.body())).hexdigest()


def _share(count: int, n: int) -> Decimal:
    """``count / n`` to six places, halves to even, held between the floor and the ceiling; exact integer arithmetic."""
    value = Decimal(round(Fraction(count * 1_000_000, n))).scaleb(-6)
    return min(max(value, P_FLOOR), P_CEIL)


def dump(rates: BarrierRates) -> bytes:
    return canonical.dumps({**rates.body(), "sha256": rates.sha256})


def _whole(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RulesError(f"{what} is a whole number")
    return value


def _instant(text: object, what: str) -> int:
    if not isinstance(text, str):
        raise RulesError(f"{what} is an RFC 3339 time")
    try:
        ms = to_ms(parse_rfc3339(text))
    except TimeError as exc:
        raise RulesError(f"{what}: {exc}") from exc
    if format_rfc3339_ms(from_ms(ms)) != text:
        raise RulesError(f"{what} is written as UTC with milliseconds")
    return ms


def load(raw: bytes) -> BarrierRates:
    """A barrier mapping from its file, or an error: canonical bytes, every member once, this rule's constants, the hash of what it says."""
    try:
        record = canonical.parse_record(raw)
    except canonical.CanonicalError as exc:
        raise RulesError(f"the mapping file is not a canonical record: {exc}") from exc
    if canonical.dumps(record) != raw:
        raise RulesError("the mapping file is not in canonical form")
    claimed = record.pop("sha256", None)
    names = {
        "rule_version",
        "min_cell",
        "k_target",
        "k_stop",
        "venue",
        "quote_class",
        "instruments",
        "period_start",
        "period_end",
        "skipped",
        "cells",
    }
    if set(record) != names:
        raise RulesError(f"the mapping file has the members {sorted(names)}")
    if (
        record["rule_version"] != RULE_VERSION
        or record["min_cell"] != MIN_CELL
        or record["k_target"] != format(K_TARGET, "f")
        or record["k_stop"] != format(K_STOP, "f")
    ):
        raise RulesError("the mapping was made for another version of the rule or other constants")
    venue, quote, instruments, cells = record["venue"], record["quote_class"], record["instruments"], record["cells"]
    if (
        not isinstance(venue, str)
        or not isinstance(quote, str)
        or not isinstance(instruments, list)
        or not all(isinstance(i, str) for i in instruments)
    ):
        raise RulesError("the venue, the quote class and the instruments are text")
    if not isinstance(cells, dict) or set(cells) != {str(h) for h in HORIZONS}:
        raise RulesError("the cells are given for every horizon")
    built: list[tuple[int, Cell]] = []
    for h in HORIZONS:
        cell = cells[str(h)]
        if not isinstance(cell, dict) or set(cell) != set(CLASSES):
            raise RulesError("a cell has target, stop, timeout and ambiguous")
        built.append((h, Cell(*(_whole(cell[name], name) for name in CLASSES))))
    rates = BarrierRates(
        tuple(built),
        venue,
        quote,
        tuple(str(i) for i in instruments),
        _instant(record["period_start"], "the period start"),
        _instant(record["period_end"], "the period end"),
        _whole(record["skipped"], "skipped"),
    )
    if claimed != rates.sha256:
        raise RulesError("the mapping file's hash is not the hash of its contents")
    return rates


def _strictly_increasing(name: str, bars: Sequence[Candle]) -> None:
    if any(a.open_time >= b.open_time for a, b in itertools.pairwise(bars)):
        raise RulesError(f"the bars of {name} are in strictly increasing time")


def _first_touch(minutes: Mapping[datetime, Candle], origin: datetime, hours: int, atr: Decimal) -> str | None:
    """``target``, ``stop``, ``timeout`` or ``ambiguous`` for a window opening at ``origin``; ``None`` if ``P0`` or a minute is missing."""
    p0_bar = minutes.get(origin - _MINUTE)
    if p0_bar is None:
        return None
    p0 = Decimal(p0_bar.close)
    target, stop = p0 + K_TARGET * atr, p0 - K_STOP * atr
    for k in range(hours * 60):
        bar = minutes.get(origin + _MINUTE * k)
        if bar is None:
            return None  # a hole before the first touch: the settlement could not decide either
        hit_target, hit_stop = Decimal(bar.high) >= target, Decimal(bar.low) <= stop
        if hit_target and hit_stop:
            return "ambiguous"
        if hit_target:
            return "target"
        if hit_stop:
            return "stop"
    return "timeout"


def calibrate(
    hourly: Mapping[str, Sequence[Candle]],
    minutes: Mapping[str, Sequence[Candle]],
    *,
    venue: str,
    quote_class: str,
    period_start_ms: int,
    period_end_ms: int,
) -> BarrierRates:
    """Count, per horizon, the non-overlapping windows of the given histories (instrument -> bars, oldest first)."""
    if set(hourly) != set(minutes):
        raise RulesError("hourly and one-minute bars are given for the same instruments")
    counts = {h: dict.fromkeys(CLASSES, 0) for h in HORIZONS}
    skipped = 0
    for name in sorted(hourly):
        bars, mins = list(hourly[name]), list(minutes[name])
        _strictly_increasing(name, bars)
        _strictly_increasing(name, mins)
        by_hour = {bar.open_time: bar for bar in bars}
        by_minute = {bar.open_time: bar for bar in mins}
        for hours in HORIZONS:
            free_from: datetime | None = None
            for bar in bars:
                origin = bar.open_time + _HOUR  # the hour boundary at which the bar has just closed
                if free_from is not None and origin < free_from:
                    continue
                run = [by_hour.get(bar.open_time - _HOUR * back) for back in reversed(range(ATR_BARS + 1))]
                if any(found is None for found in run):
                    skipped += 1
                    continue
                try:
                    atr = atr_over([found for found in run if found is not None])
                    result = None if atr == 0 else _first_touch(by_minute, origin, hours, atr)
                except ArithmeticError:  # a price the arithmetic cannot hold (an overflow or an invalid operation)
                    result = None
                if result is None:
                    skipped += 1
                    continue
                counts[hours][result] += 1
                free_from = origin + _HOUR * hours
    return BarrierRates(
        tuple((h, Cell(*(counts[h][name] for name in CLASSES))) for h in HORIZONS),
        venue,
        quote_class,
        tuple(sorted(hourly)),
        period_start_ms,
        period_end_ms,
        skipped,
    )
