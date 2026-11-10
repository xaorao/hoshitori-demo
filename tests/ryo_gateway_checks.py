"""Scenarios for attaching RYO to the gateway (``hoshitori.ryo_gateway``), runnable against the real module or a mutant of it.

A loopback fake RYO stands in for the service (synthetic answers; what is checked here is the joining, not RYO's data). The expectations are
worked out by hand from the rules in the module's docstring:

* one call to ``attach_ryo`` registers RYO under the provider name ``ryo`` with the governor at 60 calls a minute, and a call through the
  runner reaches the fake with the bearer key, is recorded as a capture of source ``ryo`` and leaves no key in the capture;
* the key is read at each request: a key that is set after the attach is used, one that is taken away fails the call with ``AUTH`` and
  sends nothing;
* an attempt shorter than RYO's own timeout is refused (the default configuration);
* ``refresh_catalogue`` gives the adapter the live tool list and returns the drift: none when the structure is as pinned, and a changed
  schema blocks exactly the tools it changed;
* a catalogue that cannot be read is an error that names the outcome, not an empty drift.
"""

from __future__ import annotations

import random
import shutil
import tempfile
import threading
import types
from collections.abc import Callable
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.connector import ErrorClass
from hoshitori.gateway import Call, CallResult, DispatchJournal, Gateway, Outcome
from hoshitori.governor import Priority
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.ryo import RyoConfig
from tests.ryo_fake_server import KEY, Behaviour, FakeRyo, Seen, error

CLOCK = SystemClock()
TESTING = RyoConfig(attempt_cap_s=3.0, min_attempt_s=0.2, testing=True)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


class Stack:
    def __init__(self, mod: types.ModuleType, tmp: Path, script: Callable[[Seen], Behaviour | None] | None = None) -> None:
        self.server = FakeRyo(script=script).start()
        self.key: str | None = KEY
        self.captures = CaptureStore(tmp / "captures")
        self.gateway = Gateway(
            self.captures,
            DispatchJournal(tmp / "dispatches.jsonl"),
            retry_policy=RetryPolicy(base_s=0.05, cap_s=0.2, rate_limit_default_wait_s=0.3),
            rng=random.Random(3),
        )
        self.attached = mod.attach_ryo(
            self.gateway,
            self.server.url,
            lambda: self.key,
            CLOCK,
            config=TESTING,
            breaker=BreakerConfig(consecutive_failures=2, cooldown_s=30),
            allow_insecure_loopback=True,
        )
        self.pool = ThreadExecutor(4)
        self.runner = Runner(self.gateway, {"ryo": self.attached.transport}, CLOCK, self.pool)
        self.loop = threading.Thread(target=self.runner.run_forever, daemon=True)
        self.loop.start()

    def call(self, operation: str, args: dict[str, object] | None = None) -> CallResult:
        call = Call("ryo", operation, args or {}, Priority.P0, CLOCK.now() + timedelta(seconds=20), "check")
        return self.runner.submit(call).result(timeout=25)

    def close(self) -> None:
        self.runner.stop()
        self.loop.join(timeout=3)
        self.pool.shutdown()
        self.server.stop()
        self.captures.close()


def check_the_attachment(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    stack = Stack(mod, tmp)
    try:
        c.eq("it registers the provider ryo", stack.attached.adapter.provider, "ryo")
        c.eq(
            "with the pinned tools and the free reads",
            sorted(stack.attached.adapter.operations() & {"scan_market", "_catalogue"}),
            ["_catalogue", "scan_market"],
        )
        result = stack.call("analyze_token", {"symbol": " SOL "})
        c.eq("a tool call goes through", (result.outcome, result.attempts), (Outcome.OK, 1))
        assert result.capture_id is not None
        capture = stack.captures.get(result.capture_id)
        c.eq(
            "and is recorded as a capture of ryo, with the arguments as they were sent",
            (capture.source, capture.request["arguments"]),
            ("ryo", {"symbol": "SOL"}),
        )
        c.eq("the bearer key reached the server", stack.server.seen[-1].headers.get("authorization"), f"Bearer {KEY}")
        c.eq(
            "and is in no header of the capture and not in its body",
            ("authorization" in capture.headers, KEY.encode() in capture.body),
            (False, False),
        )
        c.eq("the governor follows the headers RYO sent", stack.attached.governor.available(CLOCK.now()) <= 59, True)
        c.eq("the governor's own limit is 60 a minute", stack.attached.governor.available(CLOCK.now()) <= 60, True)
        seen = len(stack.server.seen)
        stack.key = None
        gone = stack.call("analyze_token", {"symbol": "SOL"})
        c.eq(
            "a key that is taken away fails the call as an authorisation failure",
            (gone.outcome, gone.error.error_class if gone.error else None),
            (Outcome.FAILED, ErrorClass.AUTH),
        )
        c.eq("and sends nothing", len(stack.server.seen), seen)
        stack.key = KEY
        c.eq("a key set again is used at the next call", stack.call("analyze_token", {"symbol": "SOL"}).outcome, Outcome.OK)
    finally:
        stack.close()
    return c.bad


def check_the_default_attempt(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    captures = CaptureStore(tmp / "captures")
    gateway = Gateway(captures, DispatchJournal(tmp / "dispatches.jsonl"), retry_policy=RetryPolicy(), rng=random.Random(1))
    attached = mod.attach_ryo(gateway, "http://127.0.0.1:9", lambda: KEY, CLOCK, allow_insecure_loopback=True)
    c.eq("an attempt is at least RYO's own timeout by default", attached.adapter.config.attempt_cap_s, 170.0)
    c.eq(
        "a rate of five calls a minute, one under RYO's fan-out limit of six, and a bucket of one, so that no minute holds more than six",
        (attached.governor.available(CLOCK.now()), attached.governor.burst, attached.governor.limit),
        (1.0, 1.0, 5.0),
    )
    from tests.ryo_checks import capture

    seen = {"x-ratelimit-limit": "60", "x-ratelimit-remaining": "53", "x-ratelimit-reset": "1793760405"}
    c.eq(
        "RYO's rate headers describe the outer limit of 60, not the binding fan-out limit, so they are not read",
        attached.adapter.rate_observation(capture("analyze_token", 200, b"{}", seen)),
        None,
    )
    c.eq("the adapter is still RYO's", (attached.adapter.provider, attached.adapter.config.attempt_cap_s), ("ryo", 170.0))
    try:
        mod.attach_ryo(gateway, "http://127.0.0.1:9", lambda: KEY, CLOCK, config=RyoConfig(attempt_cap_s=3.0, min_attempt_s=0.2))
    except ValueError as exc:
        c.eq("a shorter attempt is refused", str(exc).startswith("an attempt of 3.0 s would cut off RYO's own error"), True)
    else:
        c.bad.append("a shorter attempt than RYO's own timeout was accepted")
    captures.close()
    return c.bad


def check_the_catalogue(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    stack = Stack(mod, tmp / "ok")
    try:
        drift = mod.refresh_catalogue(stack.runner, stack.attached.adapter, stack.captures, CLOCK, 20.0)
        c.eq("the live structure is the pinned one: no drift", drift, ())
    finally:
        stack.close()

    def changed(seen: Seen) -> Behaviour | None:
        if seen.method == "GET" and seen.path.endswith("/tools"):
            real = FakeRyo().default(seen)
            return Behaviour(
                200,
                real.body.replace(
                    b'"symbol":{"type":"string","description":"Symbol."}', b'"symbol":{"type":"number","description":"Symbol."}'
                ),
                real.headers,
            )
        return None

    stack = Stack(mod, tmp / "changed", changed)
    try:
        drift = mod.refresh_catalogue(stack.runner, stack.attached.adapter, stack.captures, CLOCK, 20.0)
        c.eq(
            "a changed schema is drift, naming the tools it changed",
            sorted((d.kind, d.tool) for d in drift),
            [("SCHEMA_CHANGED", "analyze_token"), ("SCHEMA_CHANGED", "deep_analysis")],
        )
        refused = stack.call("analyze_token", {"symbol": "SOL"})
        c.eq(
            "and a tool that drifted is unavailable until a person has looked",
            (refused.outcome, refused.reason),
            (Outcome.REFUSED, "UNKNOWN_OPERATION"),
        )
        c.eq("while the others carry on", stack.call("scan_market", {"top_n": 3}).outcome, Outcome.OK)
    finally:
        stack.close()

    def down(seen: Seen) -> Behaviour | None:
        if seen.method == "GET" and seen.path.endswith("/tools"):
            return error(503, "UPSTREAM_UNAVAILABLE", "Credential verification is temporarily unavailable. Retry shortly.")
        return None

    stack = Stack(mod, tmp / "down", down)
    try:
        try:
            mod.refresh_catalogue(stack.runner, stack.attached.adapter, stack.captures, CLOCK, 20.0)
        except mod.RyoGatewayError as exc:
            c.eq(
                "a catalogue that cannot be read is an error naming the outcome (the breaker opens at the second failure)",
                str(exc),
                "RYO's tool list could not be read: BREAKER_OPEN OPEN",
            )
        else:
            c.bad.append("a catalogue that could not be read was taken for an unchanged one")
    finally:
        stack.close()
    return c.bad


class Spy:
    """A gateway that only remembers what was registered with it."""

    def __init__(self) -> None:
        self.registered: list[tuple[Any, Any, Any, int]] = []

    def register(self, adapter: Any, governor: Any, breakers: Any, *, concurrency: int = 3) -> None:
        self.registered.append((adapter, governor, breakers, concurrency))


class StubRunner:
    """A runner that answers each submitted call from a script, after a delay, and remembers the calls."""

    def __init__(self, result: CallResult, delay_s: float = 0.0) -> None:
        self.result, self.delay_s = result, delay_s
        self.calls: list[Call] = []

    def submit(self, call: Call) -> Future[CallResult]:
        self.calls.append(call)
        future: Future[CallResult] = Future()
        if self.delay_s:
            threading.Timer(self.delay_s, future.set_result, args=(self.result,)).start()
        else:
            future.set_result(self.result)
        return future


def check_what_is_registered(mod: types.ModuleType, tmp: Path) -> list[str]:
    from hoshitori.governor import RateModel
    from hoshitori.http_transport import TransportConfigError

    c = Case(mod)
    spy = Spy()
    attached = mod.attach_ryo(spy, "http://127.0.0.1:9", lambda: KEY, CLOCK, allow_insecure_loopback=True)
    (adapter, governor, breakers, concurrency) = spy.registered[0]
    c.eq("RYO is registered once, by the adapter the attachment returns", (len(spy.registered), adapter is attached.adapter), (1, True))
    c.eq("with at most four calls in flight", concurrency, 4)
    c.eq(
        "a governor of five calls a minute as a bucket that refills steadily",
        (governor.model, governor.limit, governor.window_s),
        (RateModel.TOKEN_BUCKET, 5.0, 60.0),
    )
    c.eq("the governor is the one returned", governor is attached.governor, True)
    c.eq(
        "by default a provider breaker that opens after three failures in a row",
        breakers.provider.config,
        BreakerConfig(consecutive_failures=3),
    )
    c.eq("and tools judged by the same settings", breakers.tool_config, BreakerConfig(consecutive_failures=3))
    c.eq("the breakers are the ones returned", breakers is attached.breakers, True)
    try:
        attached.adapter = None  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        c.bad.append("what an attachment returns can be replaced after it is made")
    custom = BreakerConfig(consecutive_failures=2, cooldown_s=77)
    other = Spy()
    mod.attach_ryo(
        other, "http://127.0.0.1:9", lambda: KEY, CLOCK, rate_limit=10.0, concurrency=2, breaker=custom, allow_insecure_loopback=True
    )
    (_, governor, breakers, concurrency) = other.registered[0]
    c.eq(
        "what the caller asks for is what is registered",
        (governor.limit, concurrency, breakers.provider.config, breakers.tool_config),
        (10.0, 2, custom, custom),
    )
    try:
        mod.attach_ryo(Spy(), "http://127.0.0.1:9", lambda: KEY, CLOCK)
    except TransportConfigError:
        pass
    else:
        c.bad.append("plain http was accepted without being asked for")
    return c.bad


def check_the_catalogue_call(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    captures = CaptureStore(tmp / "captures")
    adapter = mod.attach_ryo(Spy(), "http://127.0.0.1:9", lambda: KEY, CLOCK, allow_insecure_loopback=True).adapter
    failed = CallResult(1, Outcome.FAILED, None, 1, None, "EXHAUSTED", 0.0, CLOCK.now())
    runner = StubRunner(failed)
    try:
        mod.refresh_catalogue(runner, adapter, captures, CLOCK, 50.0)
    except mod.RyoGatewayError as exc:
        c.eq("a failed read is an error naming the outcome and the reason", str(exc), "RYO's tool list could not be read: FAILED EXHAUSTED")
    else:
        c.bad.append("a failed catalogue read was taken for an unchanged one")
    (call,) = runner.calls
    c.eq(
        "the read is a free call of the pseudo-operation, at the top priority, labelled as the catalogue's",
        (call.provider, call.operation, dict(call.args), call.priority, call.caller),
        ("ryo", "_catalogue", {}, Priority.P0, "ryo-catalogue"),
    )
    asked_at = CLOCK.now()
    c.eq("with a deadline that many seconds on", abs((call.deadline - asked_at).total_seconds() - 50.0) < 2.0, True)
    runner = StubRunner(CallResult(2, Outcome.OK, None, 1, None, "", 0.0, CLOCK.now()))
    try:
        mod.refresh_catalogue(runner, adapter, captures, CLOCK, 50.0)
    except mod.RyoGatewayError as exc:
        c.eq("an answer with no capture is no answer", str(exc), "RYO's tool list could not be read: OK")
    else:
        c.bad.append("an answer with no capture was accepted")
    slow = StubRunner(failed, delay_s=0.4)
    try:
        mod.refresh_catalogue(slow, adapter, captures, CLOCK, 0.1)
    except mod.RyoGatewayError as exc:
        c.eq(
            "an answer a little after the deadline is still waited for (the gateway ends the call, not this wait)",
            str(exc).startswith("RYO's tool list"),
            True,
        )
    except TimeoutError:
        c.bad.append("the wait ended with the deadline instead of five seconds after it")
    captures.close()
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "the_attachment": check_the_attachment,
    "the_default_attempt": check_the_default_attempt,
    "the_catalogue": check_the_catalogue,
    "what_is_registered": check_what_is_registered,
    "the_catalogue_call": check_the_catalogue_call,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-ryo-gateway-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
