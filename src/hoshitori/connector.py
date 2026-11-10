"""The vocabulary every provider adapter and the gateway share: one error taxonomy, and what each class means for retries and breakers.

The taxonomy is the one in ``CONNECTOR_CONTRACTS.md`` section 2 (CCXT's hierarchy is the reference for the words; ours is smaller
because everything here only reads). Classification is by the response alone (status, headers, a body prefix) and is a pure function.
A provider adapter may refine it with what only it knows (RYO's text errors, Binance's ban codes), but never invents a class.

Retry policy lives with the class so that the gateway, the adapters and the tests read one table:

* ``NEVER``       the request is not repeated (a bug in our request, an authentication problem, a ban);
* ``AFTER_WINDOW`` repeated once, by the gateway only, after the provider's stated wait (back-pressure, not failure);
* ``BACKOFF``     repeated with exponential backoff and full jitter, bounded by the request deadline (``retry``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Final


class ErrorClass(StrEnum):
    RATE_LIMIT = "RATE_LIMIT"
    BANNED = "BANNED"
    AUTH = "AUTH"
    GEO_BLOCKED = "GEO_BLOCKED"
    TIMEOUT = "TIMEOUT"
    UPSTREAM = "UPSTREAM"
    MAINTENANCE = "MAINTENANCE"
    INVALID_ARGS = "INVALID_ARGS"
    NOT_FOUND = "NOT_FOUND"
    CONTRACT_DRIFT = "CONTRACT_DRIFT"
    MALFORMED = "MALFORMED"
    UNCLASSIFIED = "UNCLASSIFIED"


class Retry(StrEnum):
    NEVER = "NEVER"
    AFTER_WINDOW = "AFTER_WINDOW"
    BACKOFF = "BACKOFF"


@dataclass(frozen=True)
class Policy:
    retry: Retry
    breaker: bool  # whether a failure of this class counts toward opening a circuit breaker
    alarm: bool = False  # whether it signals a defect of ours that someone must look at


POLICY: Final[dict[ErrorClass, Policy]] = {
    ErrorClass.RATE_LIMIT: Policy(Retry.AFTER_WINDOW, breaker=False),
    ErrorClass.BANNED: Policy(Retry.NEVER, breaker=True),
    ErrorClass.AUTH: Policy(Retry.NEVER, breaker=True),
    ErrorClass.GEO_BLOCKED: Policy(Retry.NEVER, breaker=True),
    ErrorClass.TIMEOUT: Policy(Retry.BACKOFF, breaker=True),
    ErrorClass.UPSTREAM: Policy(Retry.BACKOFF, breaker=True),
    ErrorClass.MAINTENANCE: Policy(Retry.NEVER, breaker=True),
    ErrorClass.INVALID_ARGS: Policy(Retry.NEVER, breaker=False, alarm=True),
    ErrorClass.NOT_FOUND: Policy(Retry.NEVER, breaker=False),
    ErrorClass.CONTRACT_DRIFT: Policy(Retry.NEVER, breaker=True, alarm=True),
    ErrorClass.MALFORMED: Policy(Retry.NEVER, breaker=True, alarm=True),
    ErrorClass.UNCLASSIFIED: Policy(Retry.BACKOFF, breaker=True, alarm=True),  # treated as UPSTREAM and logged for review
}

MAX_RETRY_AFTER_S: Final = 3 * 24 * 3600  # Binance bans last up to three days; a longer wait is not believed


class ConnectorError(Exception):
    """A fetch ended in a classified failure. ``retry_after_s`` is the provider's stated wait, if it gave one."""

    def __init__(self, error_class: ErrorClass, message: str, *, retry_after_s: float | None = None, trace_id: str | None = None) -> None:
        super().__init__(f"{error_class.value}: {message}")
        self.error_class = error_class
        self.message = message
        self.retry_after_s = retry_after_s
        self.trace_id = trace_id

    @property
    def policy(self) -> Policy:
        return POLICY[self.error_class]


_DELTA_SECONDS: Final = re.compile(r"[0-9]{1,9}")


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """The wait, in seconds, that a ``Retry-After`` header asks for (RFC 9110 section 10.2.3), or None if absent or unreadable.

    The header is either whole seconds or an HTTP date. A date in the past means no wait. A value beyond ``MAX_RETRY_AFTER_S`` is
    clamped, not believed. ``now`` must be timezone-aware.
    """
    if value is None:
        return None
    text = value.strip()
    if _DELTA_SECONDS.fullmatch(text):
        return float(min(int(text), MAX_RETRY_AFTER_S))
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return float(min(max((moment - now).total_seconds(), 0.0), MAX_RETRY_AFTER_S))


def classify_status(status: int | None) -> ErrorClass | None:
    """The class of an HTTP status on its own, or None for a success (2xx).

    ``None`` as the status means no response arrived at all, which the caller classifies as ``TIMEOUT`` or ``UPSTREAM`` from the
    exception it saw; it is not accepted here so that a missing status is never read as success.
    """
    if status is None:
        raise ValueError("no HTTP status: classify the transport failure instead")
    if 200 <= status < 300:
        return None
    if status == 429:
        return ErrorClass.RATE_LIMIT
    if status == 418:
        return ErrorClass.BANNED
    if status in (401, 403):
        return ErrorClass.AUTH
    if status == 451:
        return ErrorClass.GEO_BLOCKED
    if status == 404:
        return ErrorClass.NOT_FOUND
    if status == 408:
        return ErrorClass.TIMEOUT
    if 400 <= status < 500:
        return ErrorClass.INVALID_ARGS
    if 500 <= status < 600:
        return ErrorClass.UPSTREAM
    return ErrorClass.UNCLASSIFIED  # 1xx, 3xx (redirects are not followed), anything else
