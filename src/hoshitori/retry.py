"""The retry policy (``CONNECTOR_CONTRACTS`` section 4): one layer, owned by the gateway, bounded by the request's deadline.

Adapters never retry and model SDKs are configured with zero retries for provider calls, so retries cannot multiply. This module only
decides; it sleeps and sends nothing, and it takes its randomness and its clock from the caller so every rule is tested exactly.

* Classes that must never be retried (a bug in our request, an authentication problem, a ban, a changed schema) are not.
* ``RATE_LIMIT`` is back-pressure: one retry, after the provider's stated wait (a reset time stands in for ``Retry-After`` only on a
  429 that has none, and the caller resolves that before asking), never inside the window.
* ``TIMEOUT``, ``UPSTREAM`` and ``UNCLASSIFIED`` are retried with exponential backoff and full jitter, waiting at least the provider's
  ``Retry-After`` (RYO's 503 says ``retry-after: 5``). A timeout is retried at most twice.
* **The deadline, not an attempt count, stops retries**, because attempts here are long (an authenticated RYO read took 50 to 161 s, so
  two retries would stretch one call to about eight minutes). A retry is planned only if the next attempt can still get at least
  ``min_attempt_s`` before the deadline, and its timeout never extends past the deadline. A call that cannot finish is not started.
* Every attempt that was sent is charged to the provider's allowance whether or not it answered; the gateway reacquires budget for each.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from hoshitori.connector import POLICY, ErrorClass, Retry
from hoshitori.timeutil import ensure_utc

BASE_S: Final = 0.5
FACTOR: Final = 2.0
CAP_S: Final = 8.0
RATE_LIMIT_DEFAULT_WAIT_S: Final = 60.0  # when a 429 states no wait at all (the window length of the only recording)


class RetryError(ValueError):
    """An argument is not valid for the retry policy."""


@dataclass(frozen=True)
class RetryPolicy:
    base_s: float = BASE_S
    factor: float = FACTOR
    cap_s: float = CAP_S
    timeout_retries: int = 2
    backoff_retries: int = 5  # a guard beside the deadline, for UPSTREAM and UNCLASSIFIED
    rate_limit_retries: int = 1
    rate_limit_default_wait_s: float = RATE_LIMIT_DEFAULT_WAIT_S

    def __post_init__(self) -> None:
        if self.base_s <= 0 or self.factor < 1 or self.cap_s < self.base_s:
            raise RetryError("the backoff settings are not valid")
        if min(self.timeout_retries, self.backoff_retries, self.rate_limit_retries) < 0 or self.rate_limit_default_wait_s < 0:
            raise RetryError("the retry limits must not be negative")


@dataclass(frozen=True)
class Step:
    """What to do after an attempt failed. When ``retry`` is False, ``reason`` says why the call ends."""

    retry: bool
    reason: str  # RETRY, NOT_RETRYABLE, EXHAUSTED, DEADLINE
    wait_s: float = 0.0
    attempt_timeout_s: float = 0.0


@dataclass
class Retrier:
    """The retry state of one call: how many retries of each kind it has used."""

    policy: RetryPolicy
    deadline: datetime
    min_attempt_s: float
    attempt_cap_s: float  # the longest one attempt may take (the operation's own timeout)
    rng: random.Random
    attempts: int = 0
    retries: int = 0  # all retries, which sets the size of the next backoff
    rate_limited: int = 0
    timed_out: int = 0
    backed_off: int = 0
    waits: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.deadline = ensure_utc(self.deadline)
        if self.min_attempt_s <= 0 or self.attempt_cap_s < self.min_attempt_s:
            raise RetryError("an attempt needs a positive minimum no larger than its cap")

    def first_attempt(self, now: datetime) -> Step:
        """Whether the call may be started at all, and with what timeout."""
        timeout = self._timeout_at(ensure_utc(now))
        if timeout is None:
            return Step(False, "DEADLINE")
        self.attempts += 1
        return Step(True, "RETRY", 0.0, timeout)

    def _timeout_at(self, start: datetime) -> float | None:
        remaining = (self.deadline - start).total_seconds()
        if remaining < self.min_attempt_s:
            return None
        return min(self.attempt_cap_s, remaining)

    def _backoff(self) -> float:
        ceiling = min(self.policy.cap_s, self.policy.base_s * self.policy.factor**self.retries)
        return self.rng.uniform(0.0, ceiling)

    def after_failure(self, now: datetime, error_class: ErrorClass, retry_after_s: float | None = None) -> Step:
        """The next step after an attempt ended in ``error_class`` at ``now``."""
        now = ensure_utc(now)
        kind = POLICY[error_class].retry
        if kind is Retry.NEVER:
            return Step(False, "NOT_RETRYABLE")
        if kind is Retry.AFTER_WINDOW:
            if self.rate_limited >= self.policy.rate_limit_retries:
                return Step(False, "EXHAUSTED")
            wait = max(0.0, retry_after_s if retry_after_s is not None else self.policy.rate_limit_default_wait_s)
        elif error_class is ErrorClass.TIMEOUT:
            if self.timed_out >= self.policy.timeout_retries:
                return Step(False, "EXHAUSTED")
            wait = max(self._backoff(), retry_after_s or 0.0)
        else:
            if self.backed_off >= self.policy.backoff_retries:
                return Step(False, "EXHAUSTED")
            wait = max(self._backoff(), retry_after_s or 0.0)
        timeout = self._timeout_at(now + timedelta(seconds=wait))
        if timeout is None:
            return Step(False, "DEADLINE")
        self.attempts += 1
        self.retries += 1
        if kind is Retry.AFTER_WINDOW:
            self.rate_limited += 1
        elif error_class is ErrorClass.TIMEOUT:
            self.timed_out += 1
        else:
            self.backed_off += 1
        self.waits.append(wait)
        return Step(True, "RETRY", wait, timeout)
