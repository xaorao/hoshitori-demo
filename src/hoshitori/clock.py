"""Injectable clocks. Logic never reads the wall clock directly, so boundary, crash and replay tests are exact."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

from hoshitori.timeutil import ensure_utc


class Clock(Protocol):
    """A source of the current instant, always timezone-aware UTC.

    A clock may also offer ``monotonic()``, seconds that only run forward and that a change of the wall clock does not move. The ledger
    uses it to tell time passing from a step of the host clock; a clock without it is trusted as it is.
    """

    def now(self) -> datetime: ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class ManualClock:
    """A clock that only moves when told to; used by tests and by deterministic replays."""

    def __init__(self, start: datetime) -> None:
        self._now = ensure_utc(start)
        self._mono = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def set(self, moment: datetime) -> None:
        """Move to ``moment`` as time passing: a later moment is elapsed time, an earlier one is a step back that elapsed time ignores."""
        moment = ensure_utc(moment)
        self._mono += max((moment - self._now).total_seconds(), 0.0)
        self._now = moment

    def advance(self, delta: timedelta) -> None:
        self.set(self._now + delta)

    def step(self, delta: timedelta) -> None:
        """Change the wall clock without any time passing, as a wrong time source or an operator does."""
        self._now = self._now + delta
