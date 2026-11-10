"""The gateway: the one place that talks to providers (``GATEWAY_DESIGN``).

A discrete-event core. Nothing here sleeps, opens a socket or reads a clock: the caller passes ``now`` to every method, ``tick`` returns
the dispatches that may be sent now, and ``finish`` reports how each ended. A thin runner (``runner``) turns that into real time with
threads; tests drive the same core on simulated time against scripted provider traces.

What the core decides:

* **Admission.** A call must name a known provider and an operation the adapter admits, with arguments the adapter validates. Its cost
  comes from the adapter. A call that cannot finish before its deadline is refused at once (``DEADLINE_UNREACHABLE``), not queued.
* **Order.** Classes ``PA``, ``PJ``, ``P0``, ``P1``, ``P2``, ``P3`` (``governor.Priority``); inside a class the earliest deadline, then
  arrival. The judge lane has a floor: while ``PJ`` has had fewer than ``judge_floor`` calls dispatched in the last 60 seconds it goes
  ahead of everything, so a stranger completing the journey is never starved by a declared collection window.
* **Release.** A call is sent only when the provider's breaker allows it, its governor can pay and fewer than the concurrency cap are in
  flight. An open breaker refuses the call immediately with the reason (``BREAKER_OPEN``).
* **Outcomes.** Every attempt's response is stored as a ``RawCapture`` (a transport failure leaves a capture with no status), then
  classified. Success feeds the governor's headers and the breaker. A failure goes to the breaker and, for back-pressure and bans, to
  the governor; the retry policy then decides whether the call goes back in the queue after a wait or ends with its error.
* **One fetch for the same read.** Identical calls in the same namespace share one fetch (singleflight). An ``ON_DEMAND`` call may read a
  league capture that is still fresh and never writes into the league namespace.
* **Restart.** Each dispatch is journalled before it is sent; on recovery a dispatch with no capture is recorded as ``ABANDONED``
  (its cost stays counted) and is not replayed unless the caller asks again.
"""

from __future__ import annotations

import json
import os
import random
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

from hoshitori import canonical
from hoshitori.breaker import BreakerSet, Decision
from hoshitori.capture import CaptureStore, RawCapture, new_capture_id
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.governor import Governor, Priority
from hoshitori.retry import Retrier, RetryPolicy
from hoshitori.timeutil import ensure_utc, format_rfc3339_ms

LEAGUE: Final = "LEAGUE"
_RANK: Final = {Priority.PA: 1, Priority.PJ: 2, Priority.P0: 2, Priority.P1: 3, Priority.P2: 4, Priority.P3: 5}  # lower is served first
ON_DEMAND: Final = "ON_DEMAND"
NAMESPACES: Final = frozenset({LEAGUE, ON_DEMAND})
JUDGE_WINDOW_S: Final = 60.0
RATE_LIMIT_FALLBACK_S: Final = 60.0  # when a 429 states no wait


class Outcome(StrEnum):
    OK = "OK"
    BREAKER_OPEN = "BREAKER_OPEN"
    DEADLINE_UNREACHABLE = "DEADLINE_UNREACHABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REFUSED = "REFUSED"  # not admitted: unknown provider or operation, bad arguments, a namespace rule
    FAILED = "FAILED"  # the attempts ended with the error in ``CallResult.error``


class GatewayError(Exception):
    """The gateway is used wrongly (a programming error, not a provider failure)."""


class TransportFailure(Exception):
    """A fetch could not complete: no response arrived. ``error_class`` is TIMEOUT, UPSTREAM, GEO_BLOCKED and so on."""

    def __init__(self, error_class: ErrorClass, detail: str) -> None:
        super().__init__(f"{error_class.value}: {detail}")
        self.error_class = error_class
        self.detail = detail


@dataclass(frozen=True)
class RateObservation:
    """What a response's rate-limit headers said, already read by the adapter that knows the provider's header names."""

    limit: float | None = None
    remaining: float | None = None
    reset_at: datetime | None = None
    response_date: datetime | None = None


class Adapter(Protocol):
    """What a provider adapter offers the gateway (``CONNECTOR_CONTRACTS`` section 1). Adapters never retry and never decide order."""

    provider: str

    def operations(self) -> frozenset[str]: ...
    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]: ...
    def plan(self, operation: str, args: Mapping[str, Any]) -> float: ...
    def attempt_cap_s(self, operation: str) -> float: ...
    def min_attempt_s(self, operation: str) -> float: ...
    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None: ...
    def rate_observation(self, capture: RawCapture) -> RateObservation | None: ...


@dataclass(frozen=True)
class Call:
    provider: str
    operation: str
    args: Mapping[str, Any]
    priority: Priority
    deadline: datetime
    caller: str
    namespace: str = LEAGUE
    window: int | None = None  # the governor earmark this call is paid from (league cycles inside a declared window)
    max_cache_age_s: float | None = None  # an ON_DEMAND call may read a league capture at most this old


@dataclass(frozen=True)
class CallResult:
    call_id: int
    outcome: Outcome
    capture_id: str | None
    attempts: int
    error: ConnectorError | None
    reason: str  # for FAILED: why retrying ended (NOT_RETRYABLE, EXHAUSTED, DEADLINE); for REFUSED: why
    queue_wait_s: float
    finished_at: datetime
    from_cache: bool = False
    shared_with: int | None = None  # the call whose fetch this one shared
    queue_position: int | None = None


@dataclass(frozen=True)
class Dispatch:
    call_id: int
    provider: str
    operation: str
    args: Mapping[str, Any]
    capture_id: str
    timeout_s: float
    attempt: int
    started_at: datetime


@dataclass(frozen=True)
class Event:
    """One line for the status page and the metrics. It never carries arguments (a claim's text must not reach a log)."""

    at: datetime
    kind: str  # SUBMITTED, DISPATCHED, FINISHED, RETRY_PLANNED, COMPLETED
    call_id: int
    provider: str
    operation: str
    priority: str
    detail: str = ""
    queue_wait_s: float = 0.0
    budget_left: float | None = None
    breaker: str = ""
    capture_id: str | None = None


@dataclass
class ProviderState:
    adapter: Adapter
    governor: Governor
    breakers: BreakerSet
    concurrency: int = 3
    in_flight: int = 0
    judge_dispatches: deque[datetime] = field(default_factory=deque)


@dataclass
class _Pending:
    call_id: int
    call: Call
    args: dict[str, Any]
    cost: float
    key: tuple[str, str, str, str]
    submitted_at: datetime
    seq: int
    retrier: Retrier
    not_before: datetime
    waiters: dict[int, tuple[Call, datetime]] = field(default_factory=dict)
    dispatched: Dispatch | None = None
    last_error: ConnectorError | None = None
    sent: int = 0
    first_dispatch_at: datetime | None = None


def call_key(provider: str, operation: str, args: Mapping[str, Any], namespace: str) -> tuple[str, str, str, str]:
    return (provider, operation, canonical.dumps(dict(args)).decode(), namespace)


class DispatchJournal:
    """Append-only record of what was sent, written (and fsynced) before it is sent, so a restart can tell what was in flight."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, dispatch: Dispatch, priority: Priority, cost: float) -> None:
        line = (
            canonical.dumps(
                {
                    "attempt": dispatch.attempt,
                    "capture_id": dispatch.capture_id,
                    "cost": str(cost),
                    "operation": dispatch.operation,
                    "priority": priority.value,
                    "provider": dispatch.provider,
                    "sent_at": format_rfc3339_ms(dispatch.started_at),
                }
            )
            + b"\n"
        )
        with open(self.path, "ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        data = self.path.read_bytes()
        for raw in data[: data.rfind(b"\n") + 1].split(b"\n"):
            if raw:
                entries.append(json.loads(raw))
        return entries


class Gateway:
    def __init__(
        self,
        store: CaptureStore,
        journal: DispatchJournal,
        *,
        retry_policy: RetryPolicy | None = None,
        rng: random.Random | None = None,
        judge_floor: int = 1,
        queue_cap: Mapping[Priority, int] | None = None,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.store = store
        self.journal = journal
        self.retry_policy = retry_policy or RetryPolicy()
        self.rng = rng or random.Random()  # noqa: S311  (jitter for retries, not a secret)
        self.judge_floor = judge_floor
        default_cap = {Priority.P1: 200, Priority.P2: 100, Priority.P3: 100}
        self.queue_cap: dict[Priority, int] = dict(queue_cap) if queue_cap is not None else default_cap
        self.on_event = on_event
        self.providers: dict[str, ProviderState] = {}
        self.events: list[Event] = []
        self._next_id = 0
        self._seq = 0
        self._queue: dict[int, _Pending] = {}
        self._by_key: dict[tuple[str, str, str, str], int] = {}
        self._results: dict[int, CallResult] = {}
        self._league_captures: dict[tuple[str, str, str], tuple[str, datetime]] = {}

    # --- setup -------------------------------------------------------------------------------------------------------------------
    def register(self, adapter: Adapter, governor: Governor, breakers: BreakerSet, *, concurrency: int = 3) -> None:
        if adapter.provider in self.providers:
            raise GatewayError(f"{adapter.provider} is registered twice")
        if concurrency < 1:
            raise GatewayError("at least one call must be allowed in flight")
        self.providers[adapter.provider] = ProviderState(adapter, governor, breakers, concurrency)

    def declare_window(self, provider: str, start: datetime, n_calls: float, deadline: datetime) -> int:
        """Announce a collection window: ``P2`` and ``P3`` get nothing near it and ``P1`` may not take its tokens."""
        return self._state(provider).governor.add_earmark(start, n_calls, deadline)

    def end_window(self, provider: str, earmark_id: int) -> None:
        self._state(provider).governor.remove_earmark(earmark_id)

    def _state(self, provider: str) -> ProviderState:
        try:
            return self.providers[provider]
        except KeyError as exc:
            raise GatewayError(f"unknown provider {provider!r}") from exc

    # --- reading -----------------------------------------------------------------------------------------------------------------
    def result(self, call_id: int) -> CallResult | None:
        return self._results.get(call_id)

    def pending(self) -> int:
        """Calls not yet complete, including those sharing another's fetch."""
        return len(self._queue) + sum(len(p.waiters) for p in self._queue.values())

    def queue_depth(self, priority: Priority | None = None) -> int:
        return sum(1 for p in self._queue.values() if p.dispatched is None and (priority is None or p.call.priority is priority))

    def _emit(self, kind: str, now: datetime, call: Call, call_id: int, **fields: Any) -> None:
        state = self.providers.get(call.provider)
        event = Event(
            now,
            kind,
            call_id,
            call.provider,
            call.operation,
            call.priority.value,
            budget_left=None if state is None else state.governor.available(now),
            breaker="" if state is None else state.breakers.provider.state(now).value,
            **fields,
        )
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    # --- admission ---------------------------------------------------------------------------------------------------------------
    def submit(self, call: Call, now: datetime) -> int:
        """Admit a call and return its id. Its outcome is in ``result(call_id)`` once it is complete (possibly already)."""
        now = ensure_utc(now)
        self._next_id += 1
        call_id = self._next_id
        refusal = self._refusal(call)
        if refusal is not None:
            self._finish_alone(call_id, call, now, now, Outcome.REFUSED, reason=refusal)
            return call_id
        state = self.providers[call.provider]
        try:
            args = state.adapter.normalise(call.operation, call.args)
        except ConnectorError as exc:
            self._finish_alone(call_id, call, now, now, Outcome.REFUSED, error=exc, reason="INVALID_ARGS")
            return call_id
        cached = self._read_cache(call, args, now)
        if cached is not None:
            self._finish_alone(call_id, call, now, now, Outcome.OK, capture_id=cached, from_cache=True)
            return call_id
        key = call_key(call.provider, call.operation, args, call.namespace)
        shared = self._by_key.get(key)
        if shared is not None:
            owner = self._queue[shared]
            if _RANK[call.priority] < _RANK[owner.call.priority]:  # the fetch is served at the better of the two priorities
                owner.call = replace(owner.call, priority=call.priority)
            owner.waiters[call_id] = (call, now)
            self._emit("SUBMITTED", now, call, call_id, detail=f"shares the fetch of call {shared}")
            return call_id
        cap = self.queue_cap.get(call.priority)
        waiting = self.queue_depth(call.priority)
        if cap is not None and waiting >= cap:
            self._finish_alone(call_id, call, now, now, Outcome.BUDGET_EXHAUSTED, queue_position=waiting)
            return call_id
        adapter = state.adapter
        minimum, cap_s = adapter.min_attempt_s(call.operation), adapter.attempt_cap_s(call.operation)
        retrier = Retrier(self.retry_policy, call.deadline, minimum, cap_s, self.rng)
        if not retrier.first_attempt(now).retry:
            self._finish_alone(call_id, call, now, now, Outcome.DEADLINE_UNREACHABLE)
            return call_id
        self._seq += 1
        pending = _Pending(call_id, call, args, state.adapter.plan(call.operation, args), key, now, self._seq, retrier, now)
        self._queue[call_id] = pending
        self._by_key[key] = call_id
        self._emit("SUBMITTED", now, call, call_id)
        return call_id

    def _refusal(self, call: Call) -> str | None:
        if call.provider not in self.providers:
            return "UNKNOWN_PROVIDER"
        if call.operation not in self.providers[call.provider].adapter.operations():
            return "UNKNOWN_OPERATION"
        if call.namespace not in NAMESPACES:
            return "UNKNOWN_NAMESPACE"
        if call.priority is Priority.P0 and call.namespace != LEAGUE:
            return "LEAGUE_CLASS_OUTSIDE_THE_LEAGUE"
        if call.namespace == LEAGUE and call.priority in (Priority.P2, Priority.P3):
            return "ON_DEMAND_CLASS_IN_THE_LEAGUE"
        return None

    def _read_cache(self, call: Call, args: Mapping[str, Any], now: datetime) -> str | None:
        """An on-demand call may read a league capture that is still fresh; nothing else reads another namespace."""
        if call.namespace != ON_DEMAND or call.max_cache_age_s is None:
            return None
        hit = self._league_captures.get((call.provider, call.operation, canonical.dumps(dict(args)).decode()))
        if hit is None:
            return None
        capture_id, received = hit
        return capture_id if (now - received).total_seconds() <= call.max_cache_age_s else None

    # --- completing --------------------------------------------------------------------------------------------------------------
    def _finish_alone(self, call_id: int, call: Call, now: datetime, submitted_at: datetime, outcome: Outcome, **fields: Any) -> None:
        waited = (fields.pop("waited_until", now) - submitted_at).total_seconds()
        result = CallResult(
            call_id,
            outcome,
            fields.pop("capture_id", None),
            fields.pop("attempts", 0),
            fields.pop("error", None),
            fields.pop("reason", ""),
            waited,
            now,
            **fields,
        )
        self._results[call_id] = result
        self._emit("COMPLETED", now, call, call_id, detail=outcome.value, queue_wait_s=waited, capture_id=result.capture_id)

    def _complete(self, pending: _Pending, now: datetime, outcome: Outcome, **fields: Any) -> None:
        """End a pending call and every call that shared its fetch."""
        self._queue.pop(pending.call_id, None)
        self._by_key.pop(pending.key, None)
        error = fields.pop("error", pending.last_error)
        started = pending.first_dispatch_at or now  # a call that never left the queue waited until it ended
        self._finish_alone(
            pending.call_id,
            pending.call,
            now,
            pending.submitted_at,
            outcome,
            attempts=pending.sent,
            error=error,
            waited_until=started,
            **fields,
        )
        for waiter_id, (call, submitted) in pending.waiters.items():
            self._finish_alone(
                waiter_id,
                call,
                now,
                submitted,
                outcome,
                attempts=0,
                error=error,
                shared_with=pending.call_id,
                waited_until=max(started, submitted),
                **fields,
            )

    # --- ordering and release ------------------------------------------------------------------------------------------------------
    def _judge_below_floor(self, state: ProviderState, now: datetime) -> bool:
        while state.judge_dispatches and (now - state.judge_dispatches[0]).total_seconds() >= JUDGE_WINDOW_S:
            state.judge_dispatches.popleft()
        return len(state.judge_dispatches) < self.judge_floor

    def _order(self, pending: _Pending, now: datetime) -> tuple[int, datetime, int]:
        priority = pending.call.priority
        judge_below_floor = priority is Priority.PJ and self._judge_below_floor(self.providers[pending.call.provider], now)
        rank = 0 if judge_below_floor else _RANK[priority]
        return (rank, pending.call.deadline, pending.seq)

    def tick(self, now: datetime) -> list[Dispatch]:
        """Everything that may be sent at ``now``. Calls that can no longer finish, or whose breaker is open, are completed here."""
        now = ensure_utc(now)
        self._expire(now)
        sent: list[Dispatch] = []
        progressed = True
        while progressed:
            progressed = False
            for pending in sorted(self._queue.values(), key=lambda p: self._order(p, now)):
                if pending.dispatched is not None or pending.not_before > now:
                    continue
                dispatch = self._try_dispatch(pending, now)
                if dispatch is not None:
                    sent.append(dispatch)
                if dispatch is not None or pending.call_id not in self._queue:
                    progressed = True  # the state changed: look again from the top
                    break
        return sent

    def _expire(self, now: datetime) -> None:
        for pending in list(self._queue.values()):
            for waiter_id, (call, submitted) in list(pending.waiters.items()):
                if now > ensure_utc(call.deadline):
                    del pending.waiters[waiter_id]
                    self._finish_alone(
                        waiter_id,
                        call,
                        now,
                        submitted,
                        Outcome.DEADLINE_UNREACHABLE,
                        reason="WAITER_DEADLINE",
                        shared_with=pending.call_id,
                    )
            if pending.dispatched is None:
                adapter = self.providers[pending.call.provider].adapter
                if (ensure_utc(pending.call.deadline) - now).total_seconds() < adapter.min_attempt_s(pending.call.operation):
                    self._complete(pending, now, Outcome.DEADLINE_UNREACHABLE, reason="DEADLINE")

    def _try_dispatch(self, pending: _Pending, now: datetime) -> Dispatch | None:
        state = self.providers[pending.call.provider]
        call = pending.call
        operation = call.operation
        if state.in_flight >= state.concurrency:
            return None
        decision = state.breakers.allow(now, operation)
        if not decision.allowed:
            self._complete(pending, now, Outcome.BREAKER_OPEN, reason=decision.reason, queue_position=None)
            return None
        verdict = state.governor.check(now, pending.cost, call.priority)
        if not verdict.ok:
            self._give_back_trials(state, operation, decision)
            minimum = state.adapter.min_attempt_s(operation)
            ready = state.governor.earliest(now, pending.cost, call.priority)
            if ready is None or (ensure_utc(call.deadline) - ready).total_seconds() < minimum:
                self._complete(pending, now, Outcome.DEADLINE_UNREACHABLE, reason=verdict.reason, queue_position=self._position(pending))
            return None
        state.governor.try_spend(now, pending.cost, call.priority, earmark_id=call.window)
        timeout = min(state.adapter.attempt_cap_s(operation), (ensure_utc(call.deadline) - now).total_seconds())
        dispatch = Dispatch(pending.call_id, call.provider, operation, pending.args, new_capture_id(), timeout, pending.sent + 1, now)
        self.journal.record(dispatch, call.priority, pending.cost)
        pending.dispatched = dispatch
        pending.sent += 1
        if pending.first_dispatch_at is None:
            pending.first_dispatch_at = now
        state.in_flight += 1
        if call.priority is Priority.PJ:
            state.judge_dispatches.append(now)
        waited = (now - pending.submitted_at).total_seconds()
        self._emit("DISPATCHED", now, call, pending.call_id, queue_wait_s=waited, capture_id=dispatch.capture_id)
        return dispatch

    def _give_back_trials(self, state: ProviderState, operation: str, decision: Decision) -> None:
        """A call that was allowed by the breakers but not paid for did not use the half-open trials it took."""
        if decision.reason == "TRIAL":
            state.breakers.provider.release_trial()
            state.breakers.tools[operation].release_trial()

    def _position(self, pending: _Pending) -> int:
        ahead = sorted(self._queue.values(), key=lambda p: (p.call.priority.value, p.call.deadline, p.seq))
        return ahead.index(pending)

    # --- outcomes ---------------------------------------------------------------------------------------------------------------------
    def finish(self, dispatch: Dispatch, now: datetime, outcome: RawCapture | TransportFailure) -> None:
        """Report how a dispatch ended: the capture of its response, or the failure that stopped any response arriving."""
        now = ensure_utc(now)
        pending = self._queue.get(dispatch.call_id)
        if pending is None or pending.dispatched is not dispatch:
            raise GatewayError("finish for a dispatch that is not in flight")
        if not isinstance(outcome, TransportFailure) and outcome.capture_id != dispatch.capture_id:
            raise GatewayError("the capture is not the one that was dispatched")
        state = self.providers[dispatch.provider]
        call = pending.call
        state.in_flight -= 1
        pending.dispatched = None
        if isinstance(outcome, TransportFailure):
            request: dict[str, canonical.JsonValue] = {
                "arguments": dict(dispatch.args),
                "operation": dispatch.operation,
                "transport": outcome.detail[:200],
            }
            capture = RawCapture(
                dispatch.capture_id,
                dispatch.provider,
                request,
                None,
                {},
                b"",
                dispatch.started_at,
                max(now, dispatch.started_at),
                None,
            )
            error: ConnectorError | None = ConnectorError(outcome.error_class, outcome.detail)
        else:
            capture = outcome
            try:
                error = state.adapter.classify(capture, now)
            except Exception as exc:  # an adapter that cannot read an answer: the attempt failed, the retry rules decide
                error = ConnectorError(ErrorClass.UNCLASSIFIED, f"the answer could not be classified: {type(exc).__name__}: {exc}"[:200])
        try:
            self.store.append(capture)
        except Exception as exc:  # a capture that cannot be kept is not a result: the answer would have no record behind it
            error = ConnectorError(ErrorClass.UNCLASSIFIED, f"the capture could not be stored: {type(exc).__name__}: {exc}"[:200])
        observation = state.adapter.rate_observation(capture) if capture.status is not None else None
        if observation is not None:
            state.governor.observe(
                now,
                limit=observation.limit,
                remaining=observation.remaining,
                reset_at=observation.reset_at,
                response_date=observation.response_date,
            )
        detail = "OK" if error is None else error.error_class.value
        self._emit("FINISHED", now, call, pending.call_id, detail=detail, capture_id=capture.capture_id)
        if error is None:
            state.breakers.record_success(now, dispatch.operation)
            if call.namespace == LEAGUE:
                cache_key = (call.provider, call.operation, canonical.dumps(dict(pending.args)).decode())
                self._league_captures[cache_key] = (capture.capture_id, now)
            self._complete(pending, now, Outcome.OK, capture_id=capture.capture_id)
            return
        pending.last_error = error
        wait_stated = error.retry_after_s
        ban_s = None
        if error.error_class is ErrorClass.RATE_LIMIT:
            state.governor.pause(now, wait_stated if wait_stated is not None else RATE_LIMIT_FALLBACK_S)
        elif error.error_class is ErrorClass.BANNED:
            ban_s = wait_stated
            if ban_s is not None:
                state.governor.ban(now, ban_s)
        state.breakers.record_failure(now, dispatch.operation, error.error_class, ban_s=ban_s)
        step = pending.retrier.after_failure(now, error.error_class, wait_stated)
        if step.retry:
            pending.not_before = now + timedelta(seconds=step.wait_s)
            self._emit("RETRY_PLANNED", now, call, pending.call_id, detail=f"wait {step.wait_s:.2f} s")
            return
        self._complete(pending, now, Outcome.FAILED, reason=step.reason, capture_id=capture.capture_id, error=error)

    def next_wakeup(self, now: datetime) -> datetime | None:
        """The earliest time at which ``tick`` could do something new (``now`` if it already can), or None if nothing waits."""
        now = ensure_utc(now)
        times: list[datetime] = []
        for pending in self._queue.values():
            times.extend(ensure_utc(call.deadline) + timedelta(milliseconds=1) for call, _ in pending.waiters.values())  # when they lapse
            if pending.dispatched is not None:
                continue
            state = self.providers[pending.call.provider]
            minimum = state.adapter.min_attempt_s(pending.call.operation)
            times.append(ensure_utc(pending.call.deadline) - timedelta(seconds=minimum) + timedelta(milliseconds=1))  # when it expires
            if pending.not_before > now:
                times.append(pending.not_before)
                continue
            if state.in_flight >= state.concurrency:
                continue  # it waits for a dispatch to finish, which is its own wake-up
            ready = state.governor.earliest(now, pending.cost, pending.call.priority)
            if ready is not None:
                times.append(ready)
        if not times:
            return None
        return max(now, min(times))

    # --- restart ------------------------------------------------------------------------------------------------------------------------
    def recover(self, now: datetime) -> list[str]:
        """Account for dispatches that have no capture: record each as abandoned and count its cost. Returns the capture ids."""
        now = ensure_utc(now)
        abandoned: list[str] = []
        for entry in self.journal.entries():
            capture_id = entry["capture_id"]
            if capture_id in self.store:
                continue
            sent_at = ensure_utc(datetime.fromisoformat(entry["sent_at"].replace("Z", "+00:00")))
            request = {"attempt": entry["attempt"], "operation": entry["operation"], "outcome": "ABANDONED"}
            self.store.append(RawCapture(capture_id, entry["provider"], request, None, {}, b"", sent_at, max(now, sent_at), None))
            state = self.providers.get(entry["provider"])
            if state is not None and (now - sent_at).total_seconds() <= state.governor.window_s:
                state.governor.charge(now, float(entry["cost"]))
            abandoned.append(capture_id)
        return abandoned
