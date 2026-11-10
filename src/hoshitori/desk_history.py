"""One-minute history for the Desk's falsification tests: the returns of a token and of a market proxy around an announcement.

The onset and market tests read a series of one-minute returns in which the announcement sits at a known minute. This module makes that
series from candle series a venue returned. It makes nothing up: a bar that is missing, a price that is not positive or a series that does
not reach the window gives no history, and the tests then say they could not be run. The announcement's own minute is the minute that
contains it; the series starts ``PRE_MINUTES`` before it (the least the onset and market tests need before it) and
ends one horizon after.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from hoshitori import desk, falsify
from hoshitori.venue_data import CandleSeries

_ONSET: Final = falsify.OnsetParams()
_MARKET: Final = falsify.MarketParams()
# The least the two tests read before the announcement: the market test's beta window and volatility block, after their gap.
PRE_MINUTES: Final = max(
    _ONSET.sigma_gap + _ONSET.lookback, _ONSET.pre_start, _MARKET.sigma_gap + max(_MARKET.beta_minutes, _MARKET.lookback)
)
MINUTE: Final = timedelta(minutes=1)


def returns_from(series: CandleSeries, start: datetime, count: int) -> list[float] | None:
    """``count`` simple returns, close to close, the first being that of the minute that opens at ``start``; nothing if a bar is missing."""
    closes: list[float] = []
    for k in range(count + 1):
        bar = series.at(start + (k - 1) * MINUTE)
        if bar is None:
            return None
        try:
            close = float(bar.close)
        except ValueError:
            return None
        if not close > 0 or close == float("inf"):
            return None
        closes.append(close)
    return [closes[k] / closes[k - 1] - 1 for k in range(1, count + 1)]


def build(token: CandleSeries | None, market: CandleSeries | None, event_ms: int, hours: int) -> desk.History | None:
    """The history of the token (and of the market proxy, when it can be made) for an announcement at ``event_ms``."""
    if token is None:
        return None
    minute = datetime.fromtimestamp(event_ms // 60_000 * 60, tz=UTC)
    start = minute - PRE_MINUTES * MINUTE
    count = PRE_MINUTES + 60 * hours
    own = returns_from(token, start, count)
    if own is None:
        return None
    proxy = returns_from(market, start, count) if market is not None else None
    return desk.History(own, proxy, PRE_MINUTES, 60 * hours)
