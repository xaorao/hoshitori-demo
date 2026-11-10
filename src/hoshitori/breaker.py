"""Circuit breakers (``GATEWAY_DESIGN`` section 4): stop sending to a provider that is failing, and find out cheaply when it is back.

A pure state machine driven by an explicit clock.

* ``CLOSED``     calls pass. It opens after ``consecutive_failures`` counted failures in a row, or when more than ``error_rate`` of
                 the last ``window_calls`` counted outcomes failed.
* ``OPEN``       calls are refused with the reason and the time they may be tried again. The cool-down starts at ``cooldown_s`` and
                 doubles after each failed trial up to ``cooldown_max_s``; a ban opens the breaker for exactly the ban's length.
                 One health probe per ``probe_interval_s`` is allowed.
* ``HALF_OPEN``  after the cool-down, one trial call at a time. A trial that succeeds closes the breaker; one that fails opens it
                 again with a longer cool-down.

Only failures whose class counts toward a breaker (``connector.POLICY``) are recorded. Back-pressure (``RATE_LIMIT``) and errors that
mean the provider answered but our request was wrong (``INVALID_ARGS``, ``NOT_FOUND``) are neither failures nor evidence of health: they
change nothing, except that they release a trial slot so the next call can be the trial.

``BreakerSet`` is one provider's breakers together: a provider-level breaker for failures every tool shares (the credential and
allowance stage in front of RYO's tools, bans, authentication, timeouts) and one breaker per tool for the failures that belong to one
tool (a changed schema, a malformed body), so one tool changing shape does not hide the five that still work.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from hoshitori.connector import POLICY, ErrorClass
from hoshitori.timeutil import ensure_utc


class State(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class BreakerError(ValueError):
    """An argument is not valid for a breaker."""


@dataclass(frozen=True)
class BreakerConfig:
    consecutive_failures: int = 5  # priors: 5 for exchange reads, 3 for RYO
    window_calls: int = 20
    error_rate: float = 0.5  # the breaker opens when strictly more than this share of the window failed
    cooldown_s: float = 15.0
    cooldown_max_s: float = 600.0
    cooldown_factor: float = 2.0
    probe_interval_s: float = 15.0
    trial_ttl_s: float = 600.0  # a trial whose outcome never arrives (a crashed caller) frees its slot after this long

    def __post_init__(self) -> None:
        if self.consecutive_failures < 1 or self.window_calls < 1 or not 0 < self.error_rate < 1:
            raise BreakerError("the thresholds must be positive and the error rate between 0 and 1")
        if self.cooldown_s <= 0 or self.cooldown_max_s < self.cooldown_s or self.cooldown_factor < 1 or self.probe_interval_s <= 0:
            raise BreakerError("the cool-down settings are not valid")
        if self.trial_ttl_s <= 0:
            raise BreakerError("the trial time-to-live must be positive")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    state: State
    reason: str  # OK, TRIAL, OPEN, TRIAL_IN_FLIGHT
    retry_at: datetime | None  # when the next call may be tried, for a refusal


@dataclass(frozen=True)
class Transition:
    at: datetime
    before: State
    after: State
    reason: str


COUNTING: Final = frozenset(c for c in ErrorClass if POLICY[c].breaker)


@dataclass
class Breaker:
    name: str
    config: BreakerConfig
    now: datetime
    _state: State = State.CLOSED
    _streak: int = 0
    _recent: deque[bool] = field(default_factory=deque)  # True for a failure, the last ``window_calls`` counted outcomes
    _open_until: datetime | None = None
    _trips: int = 0  # consecutive opens without a success in between, for the exponential cool-down
    _trial_in_flight: bool = False
    _trial_started: datetime | None = None
    _last_probe: datetime | None = None
    reason: str = ""
    transitions: list[Transition] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.now = ensure_utc(self.now)

    # --- reading ---------------------------------------------------------------------------------------------------------------
    def state(self, now: datetime) -> State:
        self._advance(now)
        return self._state

    def _advance(self, now: datetime) -> None:
        now = ensure_utc(now)
        if now < self.now:
            raise BreakerError("time went backwards")
        self.now = now
        if self._state is State.OPEN and self._open_until is not None and now >= self._open_until:
            self._move(State.HALF_OPEN, "cool-down over")
        started = self._trial_started
        if self._trial_in_flight and started is not None and (now - started).total_seconds() >= self.config.trial_ttl_s:
            self._trial_in_flight = False  # nobody reported back: the slot is free again

    def _move(self, after: State, why: str) -> None:
        self.transitions.append(Transition(self.now, self._state, after, why))
        self._state = after

    # --- asking ---------------------------------------------------------------------------------------------------------------
    def allow(self, now: datetime) -> Decision:
        """May a call be sent now? In ``HALF_OPEN`` this takes the single trial slot; a call that was allowed must be ``record``ed."""
        self._advance(now)
        if self._state is State.CLOSED:
            return Decision(True, State.CLOSED, "OK", None)
        if self._state is State.OPEN:
            return Decision(False, State.OPEN, "OPEN", self._open_until)
        if self._trial_in_flight:
            return Decision(False, State.HALF_OPEN, "TRIAL_IN_FLIGHT", None)
        self._trial_in_flight = True
        self._trial_started = self.now
        return Decision(True, State.HALF_OPEN, "TRIAL", None)

    def release_trial(self) -> None:
        """Give back a trial slot taken by ``allow`` that was not used (another breaker refused the call)."""
        self._trial_in_flight = False

    def probe_allowed(self, now: datetime) -> bool:
        """While open, a cheap health probe (one that does not count as a call) may be sent once per probe interval."""
        self._advance(now)
        if self._state is State.CLOSED:
            return True
        if self._last_probe is not None and (self.now - self._last_probe).total_seconds() < self.config.probe_interval_s:
            return False
        self._last_probe = self.now
        return True

    # --- recording ---------------------------------------------------------------------------------------------------------------
    def record_success(self, now: datetime) -> None:
        self._advance(now)
        self._push(False)
        self._streak = 0
        if self._state is State.HALF_OPEN:
            self._trips = 0
            self._trial_in_flight = False
            self._move(State.CLOSED, "trial succeeded")
            self.reason = ""
            self._recent.clear()

    def record_failure(self, now: datetime, error_class: ErrorClass, *, ban_s: float | None = None) -> None:
        """Record a failed call. Classes that do not count (back-pressure, our own bad requests) only release a trial slot."""
        self._advance(now)
        if error_class not in COUNTING:
            self._trial_in_flight = False
            return
        if error_class is ErrorClass.BANNED and ban_s is not None:
            self._trip(f"banned for {ban_s:g} s", wait_s=max(0.0, ban_s), escalate=False)
            return
        self._push(True)
        self._streak += 1
        if self._state is State.HALF_OPEN:
            self._trip(f"trial failed: {error_class.value}")
        elif self._state is State.CLOSED:
            if self._streak >= self.config.consecutive_failures:
                self._trip(f"{self._streak} failures in a row, the last {error_class.value}")
            elif len(self._recent) >= self.config.window_calls and sum(self._recent) > self.config.error_rate * len(self._recent):
                self._trip(f"{sum(self._recent)} of the last {len(self._recent)} calls failed")

    def _push(self, failed: bool) -> None:
        self._recent.append(failed)
        while len(self._recent) > self.config.window_calls:
            self._recent.popleft()

    def _trip(self, why: str, *, wait_s: float | None = None, escalate: bool = True) -> None:
        if wait_s is None:
            wait_s = min(self.config.cooldown_max_s, self.config.cooldown_s * self.config.cooldown_factor**self._trips)
        if escalate:
            self._trips += 1
        until = self.now + timedelta(seconds=wait_s)
        if self._state is State.OPEN and self._open_until is not None and self._open_until > until:
            until = self._open_until  # a later, shorter verdict never shortens what is already in force
        self._open_until = until
        self._trial_in_flight = False
        self.reason = why
        if self._state is not State.OPEN:
            self._move(State.OPEN, why)
        else:
            self.transitions.append(Transition(self.now, State.OPEN, State.OPEN, why))


# the failures that belong to one tool rather than to the provider as a whole
TOOL_SCOPED: Final = frozenset({ErrorClass.CONTRACT_DRIFT, ErrorClass.MALFORMED})


@dataclass
class BreakerSet:
    """A provider's breakers: one for the provider, one per tool."""

    provider: Breaker
    tool_config: BreakerConfig
    tools: dict[str, Breaker] = field(default_factory=dict)

    def _tool(self, tool: str, now: datetime) -> Breaker:
        if tool not in self.tools:
            self.tools[tool] = Breaker(f"{self.provider.name}:{tool}", self.tool_config, now)
        return self.tools[tool]

    def allow(self, now: datetime, tool: str) -> Decision:
        """The provider breaker first, then the tool's. A refusal by the provider breaker does not touch the tool's trial slot; a refusal
        by the tool's breaker gives the provider's trial slot back. A call allowed while either level is half-open is that level's trial."""
        decision = self.provider.allow(now)
        if not decision.allowed:
            return decision
        tool_decision = self._tool(tool, now).allow(now)
        if not tool_decision.allowed:
            if decision.state is State.HALF_OPEN:
                self.provider.release_trial()
            return tool_decision
        return tool_decision if tool_decision.state is State.HALF_OPEN else decision  # either way a trial is reported as one

    def record_success(self, now: datetime, tool: str) -> None:
        self.provider.record_success(now)
        self._tool(tool, now).record_success(now)

    def record_failure(self, now: datetime, tool: str, error_class: ErrorClass, *, ban_s: float | None = None) -> None:
        """A tool-scoped failure counts against the tool; any other counts against the provider. Either way the other level's trial slot,
        if this call held one, is released without a verdict."""
        if error_class in TOOL_SCOPED:
            self._tool(tool, now).record_failure(now, error_class)
            self.provider.release_trial()
        else:
            self.provider.record_failure(now, error_class, ban_s=ban_s)
            self._tool(tool, now).release_trial()
