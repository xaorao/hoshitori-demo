"""A rate governor: how many requests a provider will let us send, tracked locally and corrected by what the provider says.

One governor per provider and per budget unit (``GATEWAY_DESIGN`` sections 1 to 3). It is a pure state machine: every method takes
the current instant, so rate rules, windows, bans and earmarks are tested on simulated time and replay exactly.

Two rate models are supported because RYO's is not known yet (the compatibility probe measures it) and the conservative one is
assumed until it has:

* ``FIXED_WINDOW``  ``limit`` requests per window of ``window_s`` seconds; the window rolls at its end.
* ``TOKEN_BUCKET``  capacity ``limit`` (or ``burst``, if given and smaller) refilled continuously at ``limit / window_s`` per second.
  A burst below the limit holds a stricter limit than the stated one: at most ``burst + limit`` calls enter any window of ``window_s``.

What the provider says beats what we believe, with one exception: a header describing a window that has already ended (its reset time
is at or before the response's own ``Date``) is stale and ignored, and requests we started after the response was generated are
subtracted from the remaining count it reports, because the provider could not have counted them (``observe``).

Priority classes decide who may spend what is left (``can_spend``). ``PA`` (attestation), ``P0`` (league cycles) and ``PJ`` (judge
sessions) ignore earmarks. ``P1`` to ``P3`` may spend only what leaves every declared collection window its tokens, and ``P2`` and
``P3`` get nothing at all from one refill period before a window until its deadline, so the window's first calls find the pool full.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from hoshitori.timeutil import ensure_utc


class RateModel(StrEnum):
    FIXED_WINDOW = "FIXED_WINDOW"
    TOKEN_BUCKET = "TOKEN_BUCKET"  # noqa: S105  (a rate model, not a secret)


class Priority(StrEnum):
    PA = "PA"  # attestation requests of the ledger writer, on providers no other class uses
    P0 = "P0"  # league cycles, inside a declared window
    PJ = "PJ"  # judge sessions
    P1 = "P1"  # follow refresh
    P2 = "P2"  # on-demand investigations
    P3 = "P3"  # background work


IGNORES_EARMARKS: Final = frozenset({Priority.PA, Priority.P0, Priority.PJ})
BLACKED_OUT: Final = frozenset({Priority.P2, Priority.P3})


class GovernorError(ValueError):
    """An argument is not valid for a governor."""


class _Projection:
    """The level of a governor into the future under spends we choose, without touching the governor itself."""

    def __init__(
        self,
        model: RateModel,
        limit: float,
        window_s: float,
        now: datetime,
        tokens: float,
        used: float,
        window_end: datetime,
        capacity: float | None = None,
    ) -> None:
        self.model, self.limit, self.window_s = model, limit, window_s
        self.capacity = limit if capacity is None else capacity
        self.cursor = now
        self.tokens = tokens
        self.used = used
        self.window_end = window_end

    def _move(self, t: datetime) -> None:
        if t < self.cursor:
            raise GovernorError("a projection only moves forward")
        if self.model is RateModel.TOKEN_BUCKET:
            self.tokens = min(self.capacity, self.tokens + self.limit / self.window_s * (t - self.cursor).total_seconds())
        elif t >= self.window_end:
            periods = int((t - self.window_end).total_seconds() // self.window_s) + 1
            self.window_end += timedelta(seconds=self.window_s * periods)
            self.used = 0.0
        self.cursor = t

    def advance_to(self, t: datetime) -> float:
        """Move the projection to ``t`` and return the level there."""
        self._move(t)
        return self.tokens if self.model is RateModel.TOKEN_BUCKET else self.limit - self.used

    def take(self, amount: float) -> None:
        """Spend ``amount`` at the projection's current time."""
        if self.model is RateModel.TOKEN_BUCKET:
            self.tokens -= amount
        else:
            self.used += amount


@dataclass(frozen=True)
class Verdict:
    """The answer to "may this be sent now?". ``available_at`` is the earliest instant at which the same request would be allowed if
    nothing else changed, or None if it can never be allowed (it costs more than the provider's whole allowance)."""

    ok: bool
    reason: str  # OK, PAUSED, BANNED, BLACKOUT, EARMARK, TOKENS, TOO_EXPENSIVE
    available_at: datetime | None


@dataclass
class Earmark:
    """A declared collection window: ``n`` tokens needed at ``start``, work that must be finished by ``deadline``."""

    earmark_id: int
    start: datetime
    deadline: datetime
    n: float


@dataclass
class Governor:
    name: str
    model: RateModel
    limit: float
    window_s: float
    now: datetime
    # state
    used: float = 0.0  # FIXED_WINDOW: spent in the current window
    window_end: datetime = field(init=False)
    tokens: float = field(init=False)  # TOKEN_BUCKET
    last_refill: datetime = field(init=False)
    paused_until: datetime | None = None  # a Retry-After after back-pressure
    banned_until: datetime | None = None  # a ban stated by the provider
    earmarks: list[Earmark] = field(default_factory=list)
    spends: list[datetime] = field(default_factory=list)  # starts within the last window, for the header correction
    burst: float | None = None  # TOKEN_BUCKET: the capacity if it is less than the limit
    _next_earmark: int = 0

    def __post_init__(self) -> None:
        if self.limit <= 0 or self.window_s <= 0:
            raise GovernorError("the limit and the window must be positive")
        if self.burst is not None and not 0 < self.burst <= self.limit:
            raise GovernorError("a burst is positive and no more than the limit")
        self.now = ensure_utc(self.now)
        self.window_end = self.now + timedelta(seconds=self.window_s)
        self.tokens = float(self.capacity)
        self.last_refill = self.now

    @property
    def capacity(self) -> float:
        """What a bucket holds when full: the burst if there is one, otherwise the limit."""
        return float(self.limit if self.burst is None else self.burst)

    # --- time ----------------------------------------------------------------------------------------------------------------
    @property
    def refill_per_s(self) -> float:
        return self.limit / self.window_s

    def refill_period_s(self) -> float:
        """How long the pool takes to refill one unit (token bucket) or to refill completely (fixed window)."""
        return 1.0 / self.refill_per_s if self.model is RateModel.TOKEN_BUCKET else self.window_s

    def advance(self, now: datetime) -> None:
        """Move the governor to ``now``: roll the window or refill the bucket. Time never goes backwards."""
        now = ensure_utc(now)
        if now < self.now:
            raise GovernorError("time went backwards")
        if self.model is RateModel.FIXED_WINDOW:
            if now >= self.window_end:
                periods = int((now - self.window_end).total_seconds() // self.window_s) + 1
                self.window_end += timedelta(seconds=self.window_s * periods)
                self.used = 0.0
        else:
            self.tokens = min(self.capacity, self.tokens + self.refill_per_s * (now - self.last_refill).total_seconds())
            self.last_refill = now
        self.spends = [s for s in self.spends if (now - s).total_seconds() <= self.window_s]
        self.now = now

    def available(self, now: datetime) -> float:
        self.advance(now)
        return self.limit - self.used if self.model is RateModel.FIXED_WINDOW else self.tokens

    # --- projections (no state change) ---------------------------------------------------------------------------------------
    def _simulation(self) -> _Projection:
        return _Projection(
            self.model, float(self.limit), self.window_s, self.now, self.tokens, self.used, self.window_end, float(self.capacity)
        )

    def _available_at(self, when: datetime) -> float:
        """What would be left at ``when`` if nothing more were spent."""
        return self._simulation().advance_to(when)

    def _blocked_by_pause(self, when: datetime) -> tuple[str, datetime] | None:
        if self.banned_until is not None and when < self.banned_until:
            return "BANNED", self.banned_until
        if self.paused_until is not None and when < self.paused_until:
            return "PAUSED", self.paused_until
        return None

    def _earmark_problem(self, when: datetime, cost: float, priority: Priority) -> tuple[str, datetime] | None:
        """Why ``priority`` may not spend ``cost`` at ``when`` because of a declared window, and when that stops being true."""
        if priority in IGNORES_EARMARKS:
            return None
        period = timedelta(seconds=self.refill_period_s())
        if priority in BLACKED_OUT:
            for mark in self.earmarks:
                if mark.start - period <= when <= mark.deadline:
                    return "BLACKOUT", mark.deadline
        # the arithmetic reservation: replay the spend and then every declared window in order of its start, refilling (or rolling the
        # window) between them, and require each window to find its tokens at its start
        projection = self._simulation()
        projection.advance_to(when)
        projection.take(cost)
        for mark in sorted(self.earmarks, key=lambda m: m.start):
            if mark.deadline < when:
                continue
            if projection.advance_to(max(mark.start, when)) < mark.n - 1e-9:
                return "EARMARK", mark.deadline
            projection.take(mark.n)
        return None

    def _why_not(self, when: datetime, cost: float, priority: Priority) -> tuple[str, datetime | None] | None:
        """The first reason the request may not be sent at ``when`` and a time to look again, or None if it may."""
        blocked = self._blocked_by_pause(when)
        if blocked is not None:
            return blocked
        problem = self._earmark_problem(when, cost, priority)
        if problem is not None:
            return problem
        if cost > (self.capacity if self.model is RateModel.TOKEN_BUCKET else self.limit):
            return "TOO_EXPENSIVE", None
        if self._available_at(when) >= cost - 1e-9:
            return None
        return "TOKENS", self._when_enough(cost)

    def check(self, when: datetime, cost: float, priority: Priority) -> Verdict:
        """Whether a request of ``cost`` by ``priority`` may be sent at ``when``, assuming nothing else is spent before then."""
        when = ensure_utc(when)
        if when < self.now:
            raise GovernorError("the question is about the past")
        if cost <= 0:
            raise GovernorError("a request costs something")
        why = self._why_not(when, cost, priority)
        if why is None:
            return Verdict(True, "OK", when)
        reason, look_again = why
        if reason == "EARMARK" and look_again is not None:
            # before a window starts waiting cannot help (a later spend leaves it no more tokens than an earlier one); once it is running
            # its tokens refill and the request fits at some point before the window ends, or only after it
            look_again = self._first_fit(when, look_again, cost, priority)
        return Verdict(False, reason, look_again)

    def _first_fit(self, low: datetime, high: datetime, cost: float, priority: Priority) -> datetime:
        """The earliest time in ``(low, high]`` at which the request fits, found by bisection (inside a running window the tokens it
        needs are met sooner as they refill); ``high`` if it only fits when the window ends."""
        if self._why_not(high, cost, priority) is not None:
            return high
        for _ in range(24):
            if (high - low).total_seconds() < 0.01:
                break
            middle = low + (high - low) / 2
            if self._why_not(middle, cost, priority) is None:
                high = middle
            else:
                low = middle
        return high

    def _when_enough(self, cost: float) -> datetime:
        if self.model is RateModel.FIXED_WINDOW:
            return self.window_end
        return self.now + timedelta(seconds=(cost - self.tokens) / self.refill_per_s)

    def earliest(self, now: datetime, cost: float, priority: Priority) -> datetime | None:
        """The earliest instant at which the request would be allowed, or None if it never would be."""
        self.advance(now)
        when = self.now
        for _ in range(64):  # every step moves past one blocking interval, so this ends; the bound is a guard
            verdict = self.check(when, cost, priority)
            if verdict.ok:
                return when
            if verdict.available_at is None:
                return None
            when = max(verdict.available_at, when + timedelta(milliseconds=1))
        return None

    # --- changing state -------------------------------------------------------------------------------------------------------
    def try_spend(self, now: datetime, cost: float, priority: Priority, *, earmark_id: int | None = None) -> Verdict:
        """Spend ``cost`` if allowed now. The returned verdict says whether it was spent. A call made for a declared window passes its
        ``earmark_id`` so the window's remaining need shrinks as its own calls are paid."""
        self.advance(now)
        verdict = self.check(self.now, cost, priority)
        if verdict.ok:
            if earmark_id is not None:
                for mark in self.earmarks:
                    if mark.earmark_id == earmark_id:
                        mark.n = max(0.0, mark.n - cost)
            if self.model is RateModel.FIXED_WINDOW:
                self.used += cost
            else:
                self.tokens -= cost
            self.spends.append(self.now)
        return verdict

    def charge(self, now: datetime, cost: float) -> None:
        """Count a request that was sent without asking (one found abandoned after a restart): the allowance is used whether or not it
        answered. May leave the governor in debt, which the next refill or window repays."""
        self.advance(now)
        if cost <= 0:
            raise GovernorError("a request costs something")
        if self.model is RateModel.FIXED_WINDOW:
            self.used += cost
        else:
            self.tokens -= cost
        self.spends.append(self.now)

    def pause(self, now: datetime, seconds: float) -> None:
        """Back-pressure: send nothing until ``seconds`` from now (a Retry-After). Never shortens an existing pause."""
        self.advance(now)
        until = self.now + timedelta(seconds=max(0.0, seconds))
        if self.paused_until is None or until > self.paused_until:
            self.paused_until = until

    def ban(self, now: datetime, seconds: float) -> None:
        """The provider banned us for ``seconds``. Never shortens an existing ban."""
        self.advance(now)
        until = self.now + timedelta(seconds=max(0.0, seconds))
        if self.banned_until is None or until > self.banned_until:
            self.banned_until = until

    def observe(
        self,
        now: datetime,
        *,
        limit: float | None = None,
        remaining: float | None = None,
        reset_at: datetime | None = None,
        response_date: datetime | None = None,
    ) -> bool:
        """Correct local belief with a response's rate-limit headers. Returns True if the numbers were used.

        A header set whose reset time is at or before the response's own date describes a window that had already rolled and is
        ignored. Otherwise ``remaining`` replaces the local count after subtracting the requests started since the response was
        generated, which the provider cannot have counted.
        """
        self.advance(now)
        if limit is not None and limit > 0 and limit != self.limit:
            self.limit = float(limit)
        if remaining is None:
            return False
        if reset_at is not None and response_date is not None and ensure_utc(reset_at) <= ensure_utc(response_date):
            return False
        unseen = 0
        if response_date is not None:
            unseen = sum(1 for s in self.spends if s >= ensure_utc(response_date))
        believed = max(0.0, min(float(self.limit), float(remaining) - unseen))
        if self.model is RateModel.FIXED_WINDOW:
            self.used = self.limit - believed
            if reset_at is not None and ensure_utc(reset_at) > self.now:
                self.window_end = ensure_utc(reset_at)
        else:
            self.tokens = believed
        return True

    # --- declared collection windows -------------------------------------------------------------------------------------------
    def add_earmark(self, start: datetime, n: float, deadline: datetime) -> int:
        start, deadline = ensure_utc(start), ensure_utc(deadline)
        if n <= 0 or n > self.limit or deadline < start:
            raise GovernorError("an earmark needs a positive count within the limit and a deadline not before its start")
        self._next_earmark += 1
        self.earmarks.append(Earmark(self._next_earmark, start, deadline, float(n)))
        return self._next_earmark

    def remove_earmark(self, earmark_id: int) -> None:
        self.earmarks = [m for m in self.earmarks if m.earmark_id != earmark_id]
