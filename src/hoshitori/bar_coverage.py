"""Whether a venue can settle a minute: the share of closed one-minute slots that have a bar (CF-61).

A settlement reads one bar per oracle and minute and never fills a gap, so an oracle that omits quiet minutes (Coinbase's
USDT books had a bar in 0 to 10 of 60 minutes) leaves forecasts pending until they are unsettleable. A venue that pads a
quiet minute with a flat bar of zero volume (Kraken) always answers, and the padded share is reported beside the present
share so a reader can judge what such a bar means. The gate is a pre-registered prior, ``MIN_PRESENT``; it is a measure of an
hour's data and does not replace the venue probe from the target host.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from hoshitori.venue_data import CandleSeries

MIN_PRESENT: Final = Decimal("0.98")


@dataclass(frozen=True)
class Coverage:
    venue: str
    instrument: str
    closed_slots: int  # grid slots of the window that have closed
    present: int  # of those, slots with a bar
    padded: int  # of the bars, those with zero volume (a venue's stand-in for a quiet minute)
    unknown_volume: int  # bars that carry no volume figure

    @property
    def present_share(self) -> Decimal | None:
        return None if self.closed_slots == 0 else Decimal(self.present) / Decimal(self.closed_slots)

    @property
    def padded_share(self) -> Decimal | None:
        return None if self.present == 0 else Decimal(self.padded) / Decimal(self.present)

    @property
    def can_settle(self) -> bool:
        """Whether the venue answered for enough of the closed minutes; no closed slot is no evidence, so it is not enough."""
        share = self.present_share
        return share is not None and share >= MIN_PRESENT


def _zero(volume: str | None) -> bool | None:
    if volume is None:
        return None
    try:
        return Decimal(volume) == 0
    except InvalidOperation:
        return None


def measure(series: CandleSeries) -> Coverage:
    present = len(series.candles)
    padded = unknown = 0
    for candle in series.candles:
        zero = _zero(candle.volume)
        if zero is None:
            unknown += 1
        elif zero:
            padded += 1
    return Coverage(series.venue, series.instrument, present + len(series.missing), present, padded, unknown)
