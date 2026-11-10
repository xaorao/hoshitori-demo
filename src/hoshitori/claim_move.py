"""An observation about a price move, read as something a price series can settle (contract 18 and 34.3).

"BTC fell 4% in the last 24 hours" names an asset, a direction, a size and a window, and each can be read from the words by fixed rules.
What the words leave open stays open: "today" or "recently" is not a count of hours, and a claim with no asset names nothing to look up.
A claim that speaks of the future is a forecast, not an observation, and is read by ``claim_sketch``, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

UP: Final = ("rose", "rises", "up", "gained", "gains", "climbed", "surged", "jumped", "rallied", "pumped", "increased", "higher")
DOWN: Final = ("fell", "falls", "down", "dropped", "drops", "declined", "lost", "plunged", "crashed", "dumped", "slid", "tumbled", "lower")
FUTURE: Final = ("will", "may", "might", "could", "should", "would", "going")
HOURS: Final = {"hour": 1, "hours": 1, "h": 1, "day": 24, "days": 24, "d": 24, "week": 168, "weeks": 168}
_WORD = re.compile(r"[A-Za-z']+")
_SIZE = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
_WINDOW = re.compile(r"(?:last|past|previous|in|over|within)\s+(?:the\s+)?(\d+)\s*(hours?|h|days?|d|weeks?)\b", re.IGNORECASE)
_LOOSE = ("today", "yesterday", "tonight", "recently", "lately", "this week", "this month", "this morning", "so far", "overnight")


@dataclass(frozen=True)
class MoveAssertion:
    asset: str | None
    direction: str | None  # UP or DOWN
    size_percent: Decimal | None  # the size the words state, as a positive percentage
    window_hours: int | None  # the window the words state as a count
    open: tuple[str, ...]  # what the words leave open, among asset, direction and window


def read_move(words: str, assets: tuple[str, ...]) -> MoveAssertion | None:
    """The assertion in the words, or none when they are not about a move that happened."""
    spoken = {m.group().lower() for m in _WORD.finditer(words)}
    if spoken & set(FUTURE):
        return None
    up, down = bool(spoken & set(UP)), bool(spoken & set(DOWN))
    if not (up or down):
        return None
    seen = tuple(dict.fromkeys(m.group() for m in _WORD.finditer(words) if m.group() in assets and m.group().isupper()))
    asset = seen[0] if len(seen) == 1 else None
    direction = None if up == down else ("UP" if up else "DOWN")
    size = _SIZE.search(words)
    window = _WINDOW.search(words)
    hours = None if window is None else int(window.group(1)) * HOURS[window.group(2).lower()]
    missing = tuple(n for n, found in (("asset", asset), ("direction", direction), ("window", hours)) if found is None)
    return MoveAssertion(asset, direction, None if size is None else Decimal(size.group(1)), hours, missing)


def is_loose(words: str) -> bool:
    """Whether the words give the time only loosely ("today", "recently"), which a window of hours cannot settle."""
    return any(re.search(rf"\b{re.escape(w)}\b", words, re.IGNORECASE) for w in _LOOSE)
