"""What a monthly RYO allowance can pay for: the arithmetic behind ``daily_hour`` and the protocol's scan cutoff.

A cycle that reads RYO makes ``FIXED_CALLS`` calls whatever the candidates are (``scan_market``, ``market_overview`` and
``monitor_market_sentiment_shift``) and ``PER_CANDIDATE`` for each candidate (``analyze_token`` and ``deep_analysis``), so it makes
``3 + 2n`` calls for ``n`` candidates. The candidates of a cycle are at most the scan cutoff, the event cap and the watchlist together
(the manifest's union can be smaller: an asset reached twice is one candidate). Reading once a day for ``days`` days costs
``readings_a_day * days * (3 + 2n)`` calls; this module answers which ``n`` an allowance pays for. The league itself also asks the key's own
status before a reading and asks less when the key has less left (``ryo_collect.collect_ryo``'s budget), so this is the plan, and that is
the guard.
"""

from __future__ import annotations

from typing import Final

FIXED_CALLS: Final = 3
PER_CANDIDATE: Final = 2
DAYS_A_MONTH: Final = 30


class BudgetError(ValueError):
    """The question has no answer (a count that is not a whole number of at least one, or an allowance too small for any reading)."""


def _whole(value: int, what: str, least: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise BudgetError(f"{what} is a whole number of {least} or more")


def calls_per_reading(candidates: int) -> int:
    """The calls of one cycle that reads RYO for ``candidates`` candidates."""
    _whole(candidates, "the number of candidates", 0)
    return FIXED_CALLS + PER_CANDIDATE * candidates


def calls_a_month(candidates: int, readings_a_day: int = 1, days: int = DAYS_A_MONTH) -> int:
    _whole(readings_a_day, "the readings a day", 1)
    _whole(days, "the days", 1)
    return readings_a_day * days * calls_per_reading(candidates)


def most_candidates(allowance: int, readings_a_day: int = 1, days: int = DAYS_A_MONTH) -> int:
    """The largest number of candidates whose monthly cost fits the allowance; raises ``BudgetError`` when not even one fits."""
    _whole(allowance, "the allowance", 1)
    _whole(readings_a_day, "the readings a day", 1)
    _whole(days, "the days", 1)
    per_reading = allowance // (readings_a_day * days)
    n = (per_reading - FIXED_CALLS) // PER_CANDIDATE
    if n < 1:
        raise BudgetError(f"{allowance} calls cannot pay for {readings_a_day} reading(s) a day over {days} days with one candidate")
    return n


def largest_scan_cutoff(allowance: int, event_cap: int, watchlist: int, readings_a_day: int = 1, days: int = DAYS_A_MONTH) -> int:
    """The largest scan cutoff such that scan cutoff + event cap + watchlist candidates still fit the allowance; raises ``BudgetError``
    when the event cap and the watchlist alone do not fit."""
    _whole(event_cap, "the event cap", 0)
    _whole(watchlist, "the watchlist size", 0)
    room = most_candidates(allowance, readings_a_day, days) - event_cap - watchlist
    if room < 1:
        raise BudgetError("the event cap and the watchlist alone use the whole allowance; lower them or raise the allowance")
    return room
