"""The runner: deterministic scenarios on a manual clock, real-time tests with threads, and mutants."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hoshitori import gateway, runner
from hoshitori.capture import RawCapture
from hoshitori.clock import SystemClock
from hoshitori.governor import Priority as P
from tests.gateway_support import Harness
from tests.mutation import mutant
from tests.runner_checks import failures


def test_real_module_passes_every_scenario(tmp_path: Path) -> None:
    assert failures(runner, tmp_path) == []


class SleepyTransport:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.running = 0
        self.peak = 0
        self.lock = threading.Lock()

    def fetch(self, operation: str, args: Any, timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        time.sleep(self.seconds)
        with self.lock:
            self.running -= 1
        return RawCapture(capture_id, "ryo", {"operation": operation}, 200, {}, b"{}", sent_at, datetime.now(UTC), None)


def real_runner(tmp_path: Path, transport: Any, **kwargs: Any) -> tuple[runner.Runner, runner.ThreadExecutor, threading.Thread, Harness]:
    # the harness builds the gateway, governor and breakers; the real runner replaces its simulated driver
    h = Harness(gateway, tmp_path, start=datetime.now(UTC), **kwargs)
    executor = runner.ThreadExecutor(8)
    r = runner.Runner(h.gateway, {"ryo": transport}, SystemClock(), executor)
    thread = threading.Thread(target=r.run_forever, daemon=True)
    thread.start()
    return r, executor, thread, h


FAR_FUTURE = datetime(2035, 1, 1, tzinfo=UTC)


def now_call(h: Harness, **kwargs: Any) -> Any:
    """A call from the harness with a deadline on the real clock (the harness's own are simulated seconds from a fixed start)."""
    call = h.call(**kwargs)
    return gateway.Call(call.provider, call.operation, call.args, call.priority, FAR_FUTURE, call.caller, call.namespace)


def test_threads_run_calls_in_parallel_up_to_the_cap_and_resolve_futures(tmp_path: Path) -> None:
    transport = SleepyTransport(0.15)
    r, executor, thread, h = real_runner(tmp_path, transport, limit=100, concurrency=3)
    h.governor.window_end = FAR_FUTURE  # one very long window, so only the concurrency cap shapes this test
    started = time.monotonic()
    futures = [r.submit(now_call(h, symbol=f"T{i}")) for i in range(6)]
    results = [f.result(timeout=10) for f in futures]
    took = time.monotonic() - started
    r.stop()
    thread.join(timeout=5)
    executor.shutdown()
    assert all(x.outcome is gateway.Outcome.OK for x in results)
    assert transport.peak == 3, transport.peak
    assert 0.25 < took < 3.0, took  # two waves of three
    assert not thread.is_alive()


def test_a_crashing_transport_does_not_kill_the_worker_or_the_loop(tmp_path: Path) -> None:
    calls = {"n": 0}

    class Flaky:
        def fetch(self, operation: str, args: Any, timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return RawCapture(capture_id, "ryo", {"operation": operation}, 200, {}, b"{}", sent_at, datetime.now(UTC), None)

    r, executor, thread, h = real_runner(tmp_path, Flaky(), limit=100)
    h.governor.window_end = FAR_FUTURE
    result = r.submit(now_call(h, priority=P.P0)).result(timeout=10)
    r.stop()
    thread.join(timeout=5)
    executor.shutdown()
    assert result.outcome is gateway.Outcome.OK and result.attempts == 2 and calls["n"] == 2


class SteppingBackClock:
    """The system clock, except that after ``after`` reads the wall clock steps back by ``back_s`` seconds (an NTP correction)."""

    def __init__(self, after: int, back_s: float) -> None:
        self._real = SystemClock()
        self._reads = 0
        self._after = after
        self._back = timedelta(seconds=back_s)

    def now(self) -> datetime:
        self._reads += 1
        return self._real.now() - (self._back if self._reads > self._after else timedelta(0))

    def monotonic(self) -> float:
        return self._real.monotonic()


def test_a_wall_clock_that_steps_back_does_not_end_the_loop(tmp_path: Path) -> None:
    h = Harness(gateway, tmp_path, start=datetime.now(UTC), limit=100)
    h.governor.window_end = FAR_FUTURE
    executor = runner.ThreadExecutor(4)
    r = runner.Runner(h.gateway, {"ryo": SleepyTransport(0.01)}, SteppingBackClock(after=6, back_s=30), executor)
    thread = threading.Thread(target=r.run_forever, daemon=True)
    thread.start()
    results = [r.submit(now_call(h, symbol=f"T{i}", priority=P.P0)).result(timeout=10) for i in range(6)]
    r.stop()
    thread.join(timeout=5)
    executor.shutdown()
    assert all(x.outcome is gateway.Outcome.OK for x in results)
    assert not thread.is_alive()


def test_a_pass_that_raises_is_counted_and_the_loop_goes_on(tmp_path: Path) -> None:
    r, executor, thread, h = real_runner(tmp_path, SleepyTransport(0.01), limit=100)
    h.governor.window_end = FAR_FUTURE
    original = h.gateway.tick
    seen = {"n": 0}

    def flaky(now: datetime) -> Any:
        seen["n"] += 1
        if seen["n"] == 1:
            raise OSError("disk full")
        return original(now)

    h.gateway.tick = flaky  # type: ignore[method-assign]
    result = r.submit(now_call(h, priority=P.P0)).result(timeout=10)
    r.stop()
    thread.join(timeout=5)
    executor.shutdown()
    assert result.outcome is gateway.Outcome.OK
    assert r.failed_passes >= 1 and "disk full" in r.last_error


# Redundant guard without a mutant: the clamp of the loop's sleep at zero (a negative wait is a non-blocking wait in `Condition.wait`).
MUTATIONS: dict[str, tuple[str, str]] = {
    "a call is resolved before it ends": (
        "            self._futures[call_id] = future\n            self._resolve()\n            self._wake.notify_all()",
        "            self._futures[call_id] = future\n            future.set_result(self.gateway.result(call_id) or self._placeholder())\n            self._wake.notify_all()",
    ),
    "calls that end at once are not resolved": (
        "            self._futures[call_id] = future\n            self._resolve()\n            self._wake.notify_all()",
        "            self._futures[call_id] = future\n            self._wake.notify_all()",
    ),
    "a submit is not announced to the loop": (
        "            self._resolve()\n            self._wake.notify_all()\n        return future",
        "            self._resolve()\n        return future",
    ),
    "resolved futures are kept": (
        "            self._futures.pop(call_id).set_result(result)",
        "            self._futures[call_id].set_result(result) if not self._futures[call_id].done() else None",
    ),
    "only the first result is resolved": (
        "        for call_id in [i for i in self._futures if self.gateway.result(i) is not None]:",
        "        for call_id in [i for i in self._futures if self.gateway.result(i) is not None][:1]:",
    ),
    "no result is resolved": (
        "        for call_id in [i for i in self._futures if self.gateway.result(i) is not None]:",
        "        for call_id in []:",
    ),
    "a step does not tick the gateway": (
        "            dispatches = self.gateway.tick(now)\n            self._resolve()",
        "            dispatches = []\n            self._resolve()",
    ),
    "a step does not resolve calls the tick completed": (
        "            dispatches = self.gateway.tick(now)\n            self._resolve()\n            wake",
        "            dispatches = self.gateway.tick(now)\n            wake",
    ),
    "a step ticks at the wrong time": (
        "            now = self._now()\n            dispatches = self.gateway.tick(now)",
        "            now = self._now()\n            dispatches = self.gateway.tick(now - timedelta(days=1))",
    ),
    "a step does not report when to look again": (
        "            wake = self.gateway.next_wakeup(now)\n        for dispatch",
        "            wake = None\n        for dispatch",
    ),
    "dispatches are not handed to the executor": (
        "            self.executor.submit(lambda d=dispatch: self._execute(d))  # type: ignore[misc]",
        "            pass",
    ),
    "dispatches are handed over twice": (
        "            self.executor.submit(lambda d=dispatch: self._execute(d))  # type: ignore[misc]",
        "            self.executor.submit(lambda d=dispatch: self._execute(d))  # type: ignore[misc]\n            self.executor.submit(lambda d=dispatch: self._execute(d))  # type: ignore[misc]",
    ),
    "the exchange runs under the lock": (
        "            outcome = transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)",
        "            with self._wake:\n                outcome = transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)",
    ),
    "the transport gets the wrong operation": (
        "transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)",
        'transport.fetch("other", dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)',
    ),
    "the transport gets the wrong arguments": (
        "transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)",
        "transport.fetch(dispatch.operation, {}, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)",
    ),
    "the transport gets no timeout": (
        "transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)",
        "transport.fetch(dispatch.operation, dispatch.args, 1e9, dispatch.capture_id, dispatch.started_at)",
    ),
    "the transport gets another capture id": (
        "transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, dispatch.capture_id, dispatch.started_at)",
        'transport.fetch(dispatch.operation, dispatch.args, dispatch.timeout_s, "cap-" + "f" * 32, dispatch.started_at)',
    ),
    "the provider's transport is not looked up": (
        "            transport = self.transports[dispatch.provider]",
        "            transport = next(iter(self.transports.values()))",
    ),
    "a transport failure is lost": (
        "        except TransportFailure as failure:\n            outcome = failure",
        "        except TransportFailure:\n            raise",
    ),
    "a crash in a transport kills the worker": (
        '        except Exception as exc:  # a bug in a transport must end the attempt, not the worker\n            outcome = TransportFailure(ErrorClass.UNCLASSIFIED, f"{type(exc).__name__}: {exc}"[:200])',
        "        except ZeroDivisionError as exc:\n            outcome = TransportFailure(ErrorClass.UNCLASSIFIED, str(exc))",
    ),
    "a crash is called a timeout": (
        'TransportFailure(ErrorClass.UNCLASSIFIED, f"{type(exc).__name__}: {exc}"[:200])',
        'TransportFailure(ErrorClass.TIMEOUT, f"{type(exc).__name__}: {exc}"[:200])',
    ),
    "a crash loses its description": ('f"{type(exc).__name__}: {exc}"[:200])', '"")'),
    "the end of an exchange is timed by the wrong clock": (
        "            self.gateway.finish(dispatch, self._now(), outcome)",
        "            self.gateway.finish(dispatch, dispatch.started_at, outcome)",
    ),
    "the end of an exchange is not reported": (
        "            self.gateway.finish(dispatch, self._now(), outcome)\n",
        "            pass\n",
    ),
    "the end of an exchange does not resolve calls": (
        "            self.gateway.finish(dispatch, self._now(), outcome)\n            self._resolve()\n",
        "            self.gateway.finish(dispatch, self._now(), outcome)\n",
    ),
    "the end of an exchange does not wake the loop": (
        "            self._resolve()\n            self._wake.notify_all()\n\n    # --- the loop",
        "            self._resolve()\n\n    # --- the loop",
    ),
    "stopping does not stop": (
        "            self._stopping = True\n            self._wake.notify_all()",
        "            self._wake.notify_all()",
    ),
    "stopping does not wake the loop": (
        "            self._stopping = True\n            self._wake.notify_all()",
        "            self._stopping = True",
    ),
    "the loop ignores a stop before waiting": (
        "            with self._wake:\n                if self._stopping:\n                    return\n                timeout",
        "            with self._wake:\n                timeout",
    ),
    "the loop sleeps past the next wake-up": (
        "                    timeout = min(MAX_IDLE_S, max(0.0, (wake - self._now()).total_seconds()))",
        "                    timeout = MAX_IDLE_S",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str, tmp_path: Path) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.runner", old, new)
    try:
        found = failures(module, tmp_path)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
