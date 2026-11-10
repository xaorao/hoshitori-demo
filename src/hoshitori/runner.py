"""Real time for the gateway core: a lock, a loop and worker threads around ``gateway.Gateway``.

The core is a state machine that takes ``now`` as an argument. This module supplies the clock, runs each dispatch's network exchange on a
worker thread (outside the lock, so a 160 second read blocks nothing), reports how it ended, and resolves the future of every caller whose
call is complete. It holds no policy: what to send and when is the core's decision.

``step`` is one pass of the loop and returns when to look again; ``run_forever`` waits for that time or for a notification. Tests drive
``step`` with a manual clock and an inline executor, so every ordering is exact.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any, Protocol

from hoshitori.capture import RawCapture
from hoshitori.clock import Clock
from hoshitori.connector import ErrorClass
from hoshitori.gateway import Call, CallResult, Dispatch, Gateway, TransportFailure

MAX_IDLE_S = 1.0  # a loop with nothing scheduled still looks around this often


class Transport(Protocol):
    """The network side of a provider adapter: one exchange, returning the response or raising ``TransportFailure``."""

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture: ...


class Executor(Protocol):
    def submit(self, work: Callable[[], None]) -> None: ...


class ThreadExecutor:
    def __init__(self, workers: int = 8) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gateway")

    def submit(self, work: Callable[[], None]) -> None:
        self._pool.submit(work)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


class Runner:
    def __init__(self, gateway: Gateway, transports: Mapping[str, Transport], clock: Clock, executor: Executor) -> None:
        self.gateway = gateway
        self.transports = dict(transports)
        self.clock = clock
        self.executor = executor
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._futures: dict[int, Future[CallResult]] = {}
        self._stopping = False
        self._latest: datetime | None = None
        self.failed_passes = 0  # passes of the loop that raised and were survived, for a health check
        self.last_error = ""

    def _now(self) -> datetime:
        """The clock's instant, never earlier than one already given: a step back of the host's wall clock (an NTP correction) must not
        reach the governor, which refuses time that runs backwards, and so must not end the loop. The caller holds the lock."""
        now = self.clock.now()
        if self._latest is not None and now < self._latest:
            return self._latest
        self._latest = now
        return now

    # --- callers -----------------------------------------------------------------------------------------------------------------
    def submit(self, call: Call) -> Future[CallResult]:
        """Admit a call; the future resolves with its ``CallResult`` (including the failures: an error is a result, not an exception)."""
        with self._wake:
            call_id = self.gateway.submit(call, self._now())
            future: Future[CallResult] = Future()
            self._futures[call_id] = future
            self._resolve()
            self._wake.notify_all()
        return future

    def outstanding(self) -> int:
        """How many submitted calls have no result yet."""
        with self._wake:
            return len(self._futures)

    def _resolve(self) -> None:
        """Resolve the future of every call that has a result. The caller holds the lock."""
        for call_id in [i for i in self._futures if self.gateway.result(i) is not None]:
            result = self.gateway.result(call_id)
            assert result is not None
            self._futures.pop(call_id).set_result(result)

    # --- one pass --------------------------------------------------------------------------------------------------------------------
    def step(self) -> datetime | None:
        """Send what may be sent now; return when to look again (None if nothing is waiting)."""
        with self._wake:
            now = self._now()
            dispatches = self.gateway.tick(now)
            self._resolve()
            wake = self.gateway.next_wakeup(now)
        for dispatch in dispatches:
            self.executor.submit(lambda d=dispatch: self._execute(d))  # type: ignore[misc]
        return wake

    def _execute(self, dispatch: Dispatch) -> None:
        outcome: RawCapture | TransportFailure
        try:
            transport = self.transports[dispatch.provider]
            outcome = transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)
        except TransportFailure as failure:
            outcome = failure
        except Exception as exc:  # a bug in a transport must end the attempt, not the worker
            outcome = TransportFailure(ErrorClass.UNCLASSIFIED, f"{type(exc).__name__}: {exc}"[:200])
        with self._wake:
            self.gateway.finish(dispatch, self._now(), outcome)
            self._resolve()
            self._wake.notify_all()

    # --- the loop ----------------------------------------------------------------------------------------------------------------------
    def run_forever(self) -> None:
        while True:
            try:
                wake = self.step()
            except Exception as exc:  # a pass that raises (a full disk, a bug) is counted and survived: the loop is the gateway's life
                self.failed_passes += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                wake = None
            with self._wake:
                if self._stopping:
                    return
                timeout = MAX_IDLE_S
                if wake is not None:
                    timeout = min(MAX_IDLE_S, max(0.0, (wake - self._now()).total_seconds()))
                self._wake.wait(timeout)
                if self._stopping:
                    return

    def stop(self) -> None:
        with self._wake:
            self._stopping = True
            self._wake.notify_all()
