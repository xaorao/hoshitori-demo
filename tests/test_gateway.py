"""The gateway core on simulated time: scenarios, and mutants."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import gateway
from hoshitori.connector import ErrorClass
from hoshitori.governor import Priority as P
from tests.gateway_checks import SCENARIOS, failures
from tests.mutation import mutant


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert failures(gateway, tmp_path, only=[name]) == []


outcomes = st.sampled_from(["ok", "ok", "ok", "429", "503", "401", "418", "timeout", "slow"])
calls = st.lists(
    st.tuples(
        st.floats(0, 120),
        st.sampled_from([P.P0, P.PJ, P.P1, P.P2, P.P3, P.PA]),
        st.sampled_from(["analyze", "deep"]),
        st.integers(0, 6),
        st.floats(20, 400),
    ),
    min_size=1,
    max_size=40,
)


@settings(max_examples=150, deadline=None)
@given(st.integers(0, 2**31), st.integers(1, 3), calls, st.lists(outcomes, min_size=1, max_size=15))
def test_random_traces_keep_the_invariants(seed: int, concurrency: int, plan: list, script: list) -> None:  # type: ignore[type-arg]
    import tempfile

    from tests.gateway_support import Harness, ok, response, transport

    with tempfile.TemporaryDirectory() as tmp:
        steps = iter(range(10**6))

        def answer(dispatch):  # type: ignore[no-untyped-def]
            kind = script[next(steps) % len(script)]
            if kind == "ok":
                return ok(latency=2.0)
            if kind == "429":
                return response(429, {"Retry-After": "7"})
            if kind == "503":
                return response(503, {"Retry-After": "2"})
            if kind == "401":
                return response(401)
            if kind == "418":
                return response(418, {"Retry-After": "40"})
            if kind == "slow":
                return ok(latency=30.0)
            return transport(ErrorClass.TIMEOUT, 3.0)

        h = Harness(gateway, Path(tmp), answer, limit=6, window=60, concurrency=concurrency, seed=seed)
        ids = []
        for arrive, priority, op, symbol, deadline in sorted(plan, key=lambda x: x[0]):
            h.run(arrive)
            namespace = gateway.ON_DEMAND if priority in (P.P2, P.P3) else gateway.LEAGUE
            ids.append(h.submit(h.call(op=op, symbol=f"S{symbol}", priority=priority, deadline=arrive + deadline, namespace=namespace)))
            in_flight = sum(1 for _, _, d, _ in h._heap if True)
            assert in_flight <= concurrency
        h.run(2000)
        assert h.gateway.pending() == 0, "a call was left pending"
        for cid in ids:
            result = h.result(cid)
            assert result is not None
            if result.outcome is gateway.Outcome.OK and not result.from_cache and result.shared_with is None:
                assert 200 <= h.store.get(result.capture_id).status < 300  # type: ignore[arg-type,operator]
        sent = h.sent_times()
        per_window: dict[int, float] = {}
        for time, dispatch in h.dispatched:
            per_window[int(time // 60)] = per_window.get(int(time // 60), 0.0) + {"analyze": 1.0, "deep": 3.0}[dispatch.operation]
        assert all(total <= 6 + 1e-9 for total in per_window.values()), per_window
        capture_ids = [d.capture_id for _, d in h.dispatched]
        assert len(capture_ids) == len(set(capture_ids)) == len(h.journal.entries()) == len(sent)
        assert all(cid in h.store for cid in capture_ids)
        # never more in flight than allowed: replay the dispatch and delivery times
        events = sorted([(t, 1) for t in sent] + [(t, -1) for t in _ends(h)], key=lambda e: (e[0], e[1]))
        running = 0
        for _, delta in events:
            running += delta
            assert running <= concurrency


def _ends(h):  # type: ignore[no-untyped-def]
    """When each dispatch ended: the receive time of its capture."""
    from tests.gateway_support import seconds_of

    return [seconds_of(h.store.get(d.capture_id).received_at) for _, d in h.dispatched]


# Redundant guard without a mutant: a completed call removes its key unconditionally (no other call can hold the key while it is queued).
MUTATIONS: dict[str, tuple[str, str]] = {
    # setup
    "a provider may be registered twice": (
        '        if adapter.provider in self.providers:\n            raise GatewayError(f"{adapter.provider} is registered twice")\n',
        "",
    ),
    "a concurrency of zero is allowed": ("        if concurrency < 1:", "        if concurrency < 0:"),
    "windows are declared for the wrong provider": (
        "        return self._state(provider).governor.add_earmark(start, n_calls, deadline)",
        "        return next(iter(self.providers.values())).governor.add_earmark(start, n_calls, deadline)",
    ),
    "windows are never ended": ("        self._state(provider).governor.remove_earmark(earmark_id)", "        pass"),
    "an unknown provider has a state": (
        '        except KeyError as exc:\n            raise GatewayError(f"unknown provider {provider!r}") from exc',
        "        except KeyError:\n            return next(iter(self.providers.values()))",
    ),
    # reading
    "pending ignores sharing calls": (
        "        return len(self._queue) + sum(len(p.waiters) for p in self._queue.values())",
        "        return len(self._queue)",
    ),
    "queue depth counts what is in flight": (
        "if p.dispatched is None and (priority is None or p.call.priority is priority))",
        "if (priority is None or p.call.priority is priority))",
    ),
    "queue depth ignores the class": ("(priority is None or p.call.priority is priority))", "True)"),
    "events do not say the budget left": (
        "            budget_left=None if state is None else state.governor.available(now),",
        "            budget_left=None,",
    ),
    "events do not say the breaker": (
        '            breaker="" if state is None else state.breakers.provider.state(now).value,',
        '            breaker="",',
    ),
    "events are not kept": ("        self.events.append(event)\n", ""),
    "the event hook is not called": ("        if self.on_event is not None:\n            self.on_event(event)", "        pass"),
    # refusals
    "unknown providers are admitted": ('        if call.provider not in self.providers:\n            return "UNKNOWN_PROVIDER"\n', ""),
    "unknown operations are admitted": (
        '        if call.operation not in self.providers[call.provider].adapter.operations():\n            return "UNKNOWN_OPERATION"\n',
        "",
    ),
    "unknown namespaces are admitted": ('        if call.namespace not in NAMESPACES:\n            return "UNKNOWN_NAMESPACE"\n', ""),
    "league classes may run on demand": (
        '        if call.priority is Priority.P0 and call.namespace != LEAGUE:\n            return "LEAGUE_CLASS_OUTSIDE_THE_LEAGUE"\n',
        "",
    ),
    "on-demand classes may run in the league": (
        '        if call.namespace == LEAGUE and call.priority in (Priority.P2, Priority.P3):\n            return "ON_DEMAND_CLASS_IN_THE_LEAGUE"\n',
        "",
    ),
    "investigations may run in the league": (
        'call.priority in (Priority.P2, Priority.P3):\n            return "ON_DEMAND_CLASS_IN_THE_LEAGUE"',
        'call.priority in (Priority.P3,):\n            return "ON_DEMAND_CLASS_IN_THE_LEAGUE"',
    ),
    "background work may run in the league": (
        'call.priority in (Priority.P2, Priority.P3):\n            return "ON_DEMAND_CLASS_IN_THE_LEAGUE"',
        'call.priority in (Priority.P2,):\n            return "ON_DEMAND_CLASS_IN_THE_LEAGUE"',
    ),
    "bad arguments are sent": (
        '        except ConnectorError as exc:\n            self._finish_alone(call_id, call, now, now, Outcome.REFUSED, error=exc, reason="INVALID_ARGS")\n            return call_id',
        "        except ConnectorError:\n            args = dict(call.args)",
    ),
    "refused calls lose their error": ('Outcome.REFUSED, error=exc, reason="INVALID_ARGS")', 'Outcome.REFUSED, reason="INVALID_ARGS")'),
    "arguments are not normalised": (
        "            args = state.adapter.normalise(call.operation, call.args)",
        "            args = dict(call.args)",
    ),
    # cache
    "the cache is read by the league": (
        "        if call.namespace != ON_DEMAND or call.max_cache_age_s is None:\n            return None",
        "        if call.max_cache_age_s is None:\n            return None",
    ),
    "the cache is read without asking": (
        "        if call.namespace != ON_DEMAND or call.max_cache_age_s is None:",
        "        if call.namespace != ON_DEMAND:",
    ),
    "the cache ignores arguments": (
        "        hit = self._league_captures.get((call.provider, call.operation, canonical.dumps(dict(args)).decode()))",
        '        hit = self._league_captures.get((call.provider, call.operation, "")) or next(iter(self._league_captures.values()), None)',
    ),
    "the cache ignores the age": (
        "        return capture_id if (now - received).total_seconds() <= call.max_cache_age_s else None",
        "        return capture_id",
    ),
    "the cache is exclusive at its age": ("<= call.max_cache_age_s else None", "< call.max_cache_age_s else None"),
    "failures are cached": (
        "            if call.namespace == LEAGUE:\n                cache_key",
        "            if call.namespace == LEAGUE or True:\n                cache_key",
    ),
    "on-demand captures enter the league cache": (
        "            if call.namespace == LEAGUE:\n                cache_key",
        "            if True:\n                cache_key",
    ),
    "a cached read is not marked": (
        "self._finish_alone(call_id, call, now, now, Outcome.OK, capture_id=cached, from_cache=True)",
        "self._finish_alone(call_id, call, now, now, Outcome.OK, capture_id=cached)",
    ),
    "a cached read still fetches": (
        "        if cached is not None:\n            self._finish_alone(call_id, call, now, now, Outcome.OK, capture_id=cached, from_cache=True)\n            return call_id\n",
        "",
    ),
    # sharing
    "identical calls do not share a fetch": (
        "        shared = self._by_key.get(key)\n        if shared is not None:",
        "        shared = None\n        if shared is not None:",
    ),
    "calls in different namespaces share": (
        "def call_key(provider: str, operation: str, args: Mapping[str, Any], namespace: str) -> tuple[str, str, str, str]:\n    return (provider, operation, canonical.dumps(dict(args)).decode(), namespace)",
        'def call_key(provider: str, operation: str, args: Mapping[str, Any], namespace: str) -> tuple[str, str, str, str]:\n    return (provider, operation, canonical.dumps(dict(args)).decode(), "")',
    ),
    "calls with other arguments share": (
        "    return (provider, operation, canonical.dumps(dict(args)).decode(), namespace)",
        '    return (provider, operation, "", namespace)',
    ),
    "calls to other operations share": (
        "    return (provider, operation, canonical.dumps(dict(args)).decode(), namespace)",
        '    return (provider, "", canonical.dumps(dict(args)).decode(), namespace)',
    ),
    "a sharing call is not told what it shares": (
        "                shared_with=pending.call_id,\n                waited_until=max(started, submitted),",
        "                waited_until=max(started, submitted),",
    ),
    "a sharing call is not completed": (
        "        for waiter_id, (call, submitted) in pending.waiters.items():\n            self._finish_alone(",
        "        for waiter_id, (call, submitted) in []:\n            self._finish_alone(",
    ),
    "a sharing call does not get the error": (
        "                attempts=0,\n                error=error,\n                shared_with=pending.call_id,",
        "                attempts=0,\n                error=None,\n                shared_with=pending.call_id,",
    ),
    "a waiter's deadline is ignored": ("                if now > ensure_utc(call.deadline):", "                if False:"),
    "a waiter's deadline is exclusive": (
        "                if now > ensure_utc(call.deadline):",
        "                if now >= ensure_utc(call.deadline):",
    ),
    "a waiter outlives its fetch": ("                    del pending.waiters[waiter_id]\n", ""),
    # caps
    "the on-demand queue is not capped": ("        if cap is not None and waiting >= cap:", "        if False:"),
    "the cap is one too generous": ("        if cap is not None and waiting >= cap:", "        if cap is not None and waiting > cap:"),
    "the cap is one too tight": ("        if cap is not None and waiting >= cap:", "        if cap is not None and waiting >= cap - 1:"),
    "the default cap on investigations is wrong": (
        "default_cap = {Priority.P1: 200, Priority.P2: 100, Priority.P3: 100}",
        "default_cap = {Priority.P1: 200, Priority.P2: 99, Priority.P3: 100}",
    ),
    "the queue position of a refusal is lost": ("Outcome.BUDGET_EXHAUSTED, queue_position=waiting)", "Outcome.BUDGET_EXHAUSTED)"),
    # deadlines
    "a call that cannot finish is queued": (
        "        if not retrier.first_attempt(now).retry:\n            self._finish_alone(call_id, call, now, now, Outcome.DEADLINE_UNREACHABLE)\n            return call_id\n",
        "",
    ),
    "the retrier gets the wrong deadline": (
        "        retrier = Retrier(self.retry_policy, call.deadline, minimum, cap_s, self.rng)",
        "        retrier = Retrier(self.retry_policy, call.deadline + timedelta(days=1), minimum, cap_s, self.rng)",
    ),
    "the retrier gets the wrong minimum": (
        "        retrier = Retrier(self.retry_policy, call.deadline, minimum, cap_s, self.rng)",
        "        retrier = Retrier(self.retry_policy, call.deadline, 0.001, cap_s, self.rng)",
    ),
    "expired calls are kept": (
        '                if (ensure_utc(pending.call.deadline) - now).total_seconds() < adapter.min_attempt_s(pending.call.operation):\n                    self._complete(pending, now, Outcome.DEADLINE_UNREACHABLE, reason="DEADLINE")',
        "                pass",
    ),
    "calls expire one tick early": (
        "                if (ensure_utc(pending.call.deadline) - now).total_seconds() < adapter.min_attempt_s(pending.call.operation):",
        "                if (ensure_utc(pending.call.deadline) - now).total_seconds() <= adapter.min_attempt_s(pending.call.operation):",
    ),
    "expiry ignores the minimum attempt": (
        "                if (ensure_utc(pending.call.deadline) - now).total_seconds() < adapter.min_attempt_s(pending.call.operation):",
        "                if (ensure_utc(pending.call.deadline) - now).total_seconds() < 0:",
    ),
    "in-flight calls are expired": (
        "            if pending.dispatched is None:\n                adapter = self.providers[pending.call.provider].adapter",
        "            if True:\n                adapter = self.providers[pending.call.provider].adapter",
    ),
    "a call that waits for tokens past its deadline is kept": (
        "            if ready is None or (ensure_utc(call.deadline) - ready).total_seconds() < minimum:\n                self._complete(",
        "            if False:\n                self._complete(",
    ),
    "the wait for tokens ignores the minimum attempt": (
        "            if ready is None or (ensure_utc(call.deadline) - ready).total_seconds() < minimum:",
        "            if ready is None or (ensure_utc(call.deadline) - ready).total_seconds() < 0:",
    ),
    "an impossible request is kept waiting": (
        "            if ready is None or (ensure_utc(call.deadline) - ready).total_seconds() < minimum:",
        "            if (ensure_utc(call.deadline) - (ready or now)).total_seconds() < minimum:",
    ),
    "the reason for waiting is lost": (
        "reason=verdict.reason, queue_position=self._position(pending))",
        'reason="", queue_position=self._position(pending))',
    ),
    "the queue position is lost": ("reason=verdict.reason, queue_position=self._position(pending))", "reason=verdict.reason)"),
    "the attempt may run past the deadline": (
        "        timeout = min(state.adapter.attempt_cap_s(operation), (ensure_utc(call.deadline) - now).total_seconds())",
        "        timeout = state.adapter.attempt_cap_s(operation)",
    ),
    "the attempt ignores the operation's cap": (
        "        timeout = min(state.adapter.attempt_cap_s(operation), (ensure_utc(call.deadline) - now).total_seconds())",
        "        timeout = (ensure_utc(call.deadline) - now).total_seconds()",
    ),
    # ordering
    "the judge floor never applies": (
        "        judge_below_floor = priority is Priority.PJ and self._judge_below_floor(self.providers[pending.call.provider], now)",
        "        judge_below_floor = False",
    ),
    "the judge is always first": (
        "        judge_below_floor = priority is Priority.PJ and self._judge_below_floor(self.providers[pending.call.provider], now)",
        "        judge_below_floor = priority is Priority.PJ",
    ),
    "any class gets the judge floor": (
        "        judge_below_floor = priority is Priority.PJ and self._judge_below_floor(self.providers[pending.call.provider], now)",
        "        judge_below_floor = priority is not Priority.PA and self._judge_below_floor(self.providers[pending.call.provider], now)",
    ),
    "the judge floor is exclusive": (
        "        return len(state.judge_dispatches) < self.judge_floor",
        "        return len(state.judge_dispatches) <= self.judge_floor",
    ),
    "the judge floor is one too few": (
        "        return len(state.judge_dispatches) < self.judge_floor",
        "        return len(state.judge_dispatches) < self.judge_floor - 1",
    ),
    "judge dispatches are not counted": (
        "        if call.priority is Priority.PJ:\n            state.judge_dispatches.append(now)",
        "        pass",
    ),
    "all dispatches count for the judge": (
        "        if call.priority is Priority.PJ:\n            state.judge_dispatches.append(now)",
        "        state.judge_dispatches.append(now)",
    ),
    "judge dispatches never age": (
        "        while state.judge_dispatches and (now - state.judge_dispatches[0]).total_seconds() >= JUDGE_WINDOW_S:\n            state.judge_dispatches.popleft()\n",
        "",
    ),
    "judge dispatches age one tick late": (
        "(now - state.judge_dispatches[0]).total_seconds() >= JUDGE_WINDOW_S:",
        "(now - state.judge_dispatches[0]).total_seconds() > JUDGE_WINDOW_S:",
    ),
    "the judge window is the wrong length": ("JUDGE_WINDOW_S: Final = 60.0", "JUDGE_WINDOW_S: Final = 30.0"),
    "attestation is not served before the league": (
        "{Priority.PA: 1, Priority.PJ: 2, Priority.P0: 2,",
        "{Priority.PA: 2, Priority.PJ: 2, Priority.P0: 2,",
    ),
    "follow refresh is served before the league": (
        "Priority.P1: 3, Priority.P2: 4, Priority.P3: 5}",
        "Priority.P1: 2, Priority.P2: 4, Priority.P3: 5}",
    ),
    "investigations are served before follow refresh": (
        "Priority.P1: 3, Priority.P2: 4, Priority.P3: 5}",
        "Priority.P1: 3, Priority.P2: 3, Priority.P3: 5}",
    ),
    "background work is served before investigations": (
        "Priority.P1: 3, Priority.P2: 4, Priority.P3: 5}",
        "Priority.P1: 3, Priority.P2: 4, Priority.P3: 4}",
    ),
    "the deadline does not order calls": (
        "        return (rank, pending.call.deadline, pending.seq)",
        "        return (rank, datetime(2020, 1, 1, tzinfo=UTC), pending.seq)",
    ),
    "arrival does not order calls": (
        "        return (rank, pending.call.deadline, pending.seq)",
        "        return (rank, pending.call.deadline, -pending.seq)",
    ),
    "the class does not order calls": (
        "        return (rank, pending.call.deadline, pending.seq)",
        "        return (0, pending.call.deadline, pending.seq)",
    ),
    # release
    "the concurrency cap is ignored": (
        "        if state.in_flight >= state.concurrency:\n            return None\n        decision",
        "        decision",
    ),
    "the concurrency cap is one too high": (
        "        if state.in_flight >= state.concurrency:\n            return None\n        decision",
        "        if state.in_flight > state.concurrency:\n            return None\n        decision",
    ),
    "a call waits for its retry time": (
        "                if pending.dispatched is not None or pending.not_before > now:\n                    continue",
        "                if pending.dispatched is not None:\n                    continue",
    ),
    "a dispatched call is sent again": (
        "                if pending.dispatched is not None or pending.not_before > now:\n                    continue",
        "                if pending.not_before > now:\n                    continue",
    ),
    "the breaker is not asked": (
        "        decision = state.breakers.allow(now, operation)\n        if not decision.allowed:\n            self._complete(pending, now, Outcome.BREAKER_OPEN, reason=decision.reason, queue_position=None)\n            return None",
        "        decision = state.breakers.allow(now, operation)",
    ),
    "a refused call does not say why": (
        "Outcome.BREAKER_OPEN, reason=decision.reason, queue_position=None)",
        'Outcome.BREAKER_OPEN, reason="", queue_position=None)',
    ),
    "the budget is not asked": (
        "        verdict = state.governor.check(now, pending.cost, call.priority)\n        if not verdict.ok:",
        "        verdict = state.governor.check(now, pending.cost, call.priority)\n        if False:",
    ),
    "the budget is not paid": ("        state.governor.try_spend(now, pending.cost, call.priority, earmark_id=call.window)\n", ""),
    "the window is not paid down": (
        "state.governor.try_spend(now, pending.cost, call.priority, earmark_id=call.window)",
        "state.governor.try_spend(now, pending.cost, call.priority)",
    ),
    "the cost is wrong": (
        "state.governor.try_spend(now, pending.cost, call.priority, earmark_id=call.window)",
        "state.governor.try_spend(now, 1.0, call.priority, earmark_id=call.window)",
    ),
    "trials are not given back": (
        '        if decision.reason == "TRIAL":\n            state.breakers.provider.release_trial()\n            state.breakers.tools[operation].release_trial()',
        "        pass",
    ),
    "the dispatch is not journalled": ("        self.journal.record(dispatch, call.priority, pending.cost)\n", ""),
    "the cost is not journalled": (
        "self.journal.record(dispatch, call.priority, pending.cost)",
        "self.journal.record(dispatch, call.priority, 0.0)",
    ),
    "in-flight calls are not counted": (
        "        pending.sent += 1\n        if pending.first_dispatch_at is None:\n            pending.first_dispatch_at = now\n        state.in_flight += 1",
        "        pending.sent += 1\n        if pending.first_dispatch_at is None:\n            pending.first_dispatch_at = now",
    ),
    "attempts are not counted": ("        pending.sent += 1\n", ""),
    "the first dispatch time is not kept": (
        "        if pending.first_dispatch_at is None:\n            pending.first_dispatch_at = now",
        "        pending.first_dispatch_at = now",
    ),
    "queue wait is not recorded": ("        started = pending.first_dispatch_at or now", "        started = now"),
    "the attempt number is wrong": (
        "Dispatch(pending.call_id, call.provider, operation, pending.args, new_capture_id(), timeout, pending.sent + 1, now)",
        "Dispatch(pending.call_id, call.provider, operation, pending.args, new_capture_id(), timeout, 1, now)",
    ),
    "every dispatch gets the same capture id": ("pending.args, new_capture_id(), timeout,", 'pending.args, "cap-" + "0" * 32, timeout,'),
    "the loop stops after one dispatch": (
        "                if dispatch is not None or pending.call_id not in self._queue:\n                    progressed = True  # the state changed: look again from the top\n                    break",
        "                if dispatch is not None or pending.call_id not in self._queue:\n                    return sent + ([dispatch] if dispatch else [])",
    ),
    "the loop does not look again": (
        "                    progressed = True  # the state changed: look again from the top\n                    break",
        "                    break",
    ),
    "the queue is not expired before a tick": (
        "        now = ensure_utc(now)\n        self._expire(now)\n        sent",
        "        now = ensure_utc(now)\n        sent",
    ),
    # outcomes
    "a finish for nothing is accepted": (
        '        if pending is None or pending.dispatched is not dispatch:\n            raise GatewayError("finish for a dispatch that is not in flight")',
        '        if pending is None:\n            raise GatewayError("finish for a dispatch that is not in flight")',
    ),
    "a finish twice is accepted": ("        if pending is None or pending.dispatched is not dispatch:", "        if pending is None:"),
    "a wrong capture is accepted": (
        '        if not isinstance(outcome, TransportFailure) and outcome.capture_id != dispatch.capture_id:\n            raise GatewayError("the capture is not the one that was dispatched")\n',
        "",
    ),
    "a wrong capture is accepted after the state changes": (
        '        if not isinstance(outcome, TransportFailure) and outcome.capture_id != dispatch.capture_id:\n            raise GatewayError("the capture is not the one that was dispatched")\n        state = self.providers[dispatch.provider]',
        "        state = self.providers[dispatch.provider]",
    ),
    "in-flight calls are not released": ("        state.in_flight -= 1\n", ""),
    "the call stays dispatched": (
        "        pending.dispatched = None\n        if isinstance(outcome, TransportFailure):",
        "        if isinstance(outcome, TransportFailure):",
    ),
    "a transport failure leaves no capture": ("            self.store.append(capture)\n", "            pass\n"),
    "a transport failure keeps no request": (
        '                "arguments": dict(dispatch.args),\n                "operation": dispatch.operation,\n                "transport": outcome.detail[:200],',
        '                "transport": outcome.detail[:200],',
    ),
    "a transport failure invents a status": (
        '                request,\n                None,\n                {},\n                b"",',
        '                request,\n                599,\n                {},\n                b"",',
    ),
    "a transport failure is not classified": (
        "            error: ConnectorError | None = ConnectorError(outcome.error_class, outcome.detail)",
        "            error = None",
    ),
    "a response is not classified": ("            error = state.adapter.classify(capture, now)", "            error = None"),
    "rate headers are never read": (
        "        observation = state.adapter.rate_observation(capture) if capture.status is not None else None",
        "        observation = None",
    ),
    "rate headers are read from transport failures": ("if capture.status is not None else None", "if True else None"),
    "the remaining count is not used": (
        "                remaining=observation.remaining,",
        "                remaining=None,",
    ),
    "success does not close the breaker": ("            state.breakers.record_success(now, dispatch.operation)\n", ""),
    "failure is not recorded by the breaker": (
        "        state.breakers.record_failure(now, dispatch.operation, error.error_class, ban_s=ban_s)\n",
        "",
    ),
    "a ban loses its length": (
        "        state.breakers.record_failure(now, dispatch.operation, error.error_class, ban_s=ban_s)",
        "        state.breakers.record_failure(now, dispatch.operation, error.error_class)",
    ),
    "a ban does not stop the governor": (
        "            if ban_s is not None:\n                state.governor.ban(now, ban_s)",
        "            pass",
    ),
    "a rate limit does not pause the governor": (
        "            state.governor.pause(now, wait_stated if wait_stated is not None else RATE_LIMIT_FALLBACK_S)",
        "            pass",
    ),
    "a rate limit pauses for the wrong time": (
        "state.governor.pause(now, wait_stated if wait_stated is not None else RATE_LIMIT_FALLBACK_S)",
        "state.governor.pause(now, 1.0)",
    ),
    "a rate limit without a wait pauses for nothing": (
        "state.governor.pause(now, wait_stated if wait_stated is not None else RATE_LIMIT_FALLBACK_S)",
        "state.governor.pause(now, wait_stated or 0.0)",
    ),
    "the fallback wait is wrong": ("RATE_LIMIT_FALLBACK_S: Final = 60.0", "RATE_LIMIT_FALLBACK_S: Final = 5.0"),
    "the last error is not kept": ("        pending.last_error = error\n", ""),
    "failures are not retried": (
        "        if step.retry:\n            pending.not_before = now + timedelta(seconds=step.wait_s)",
        "        if False:\n            pending.not_before = now + timedelta(seconds=step.wait_s)",
    ),
    "retries do not wait": ("            pending.not_before = now + timedelta(seconds=step.wait_s)", "            pass"),
    "retries wait twice": (
        "            pending.not_before = now + timedelta(seconds=step.wait_s)",
        "            pending.not_before = now + timedelta(seconds=2 * step.wait_s)",
    ),
    "retries are not announced": (
        '            self._emit("RETRY_PLANNED", now, call, pending.call_id, detail=f"wait {step.wait_s:.2f} s")\n',
        "",
    ),
    "a failed call does not say why it ended": (
        "self._complete(pending, now, Outcome.FAILED, reason=step.reason, capture_id=capture.capture_id, error=error)",
        "self._complete(pending, now, Outcome.FAILED, capture_id=capture.capture_id, error=error)",
    ),
    "a failed call loses its capture": (
        "self._complete(pending, now, Outcome.FAILED, reason=step.reason, capture_id=capture.capture_id, error=error)",
        "self._complete(pending, now, Outcome.FAILED, reason=step.reason, error=error)",
    ),
    "a failed call loses its error": (
        "self._complete(pending, now, Outcome.FAILED, reason=step.reason, capture_id=capture.capture_id, error=error)",
        "self._complete(pending, now, Outcome.FAILED, reason=step.reason, capture_id=capture.capture_id, error=None)",
    ),
    "a successful call is not completed": (
        "            self._complete(pending, now, Outcome.OK, capture_id=capture.capture_id)\n            return",
        "            return",
    ),
    "a completed call stays queued": ("        self._queue.pop(pending.call_id, None)\n", ""),
    "a completed call keeps its key": ("        self._by_key.pop(pending.key, None)\n", ""),
    # wake-ups
    "the gateway never wakes for a deadline": (
        "            times.append(ensure_utc(pending.call.deadline) - timedelta(seconds=minimum) + timedelta(milliseconds=1))  # when it expires\n",
        "",
    ),
    "the gateway never wakes for a retry": (
        "            if pending.not_before > now:\n                times.append(pending.not_before)\n                continue\n",
        "",
    ),
    "the gateway never wakes for tokens": (
        "            ready = state.governor.earliest(now, pending.cost, pending.call.priority)\n            if ready is not None:\n                times.append(ready)\n        if not times:",
        "        if not times:",
    ),
    "the gateway never wakes for a waiter": (
        "            times.extend(ensure_utc(call.deadline) + timedelta(milliseconds=1) for call, _ in pending.waiters.values())  # when they lapse\n",
        "",
    ),
    "a blocked call wakes the gateway at once": (
        "            if state.in_flight >= state.concurrency:\n                continue  # it waits for a dispatch to finish, which is its own wake-up\n",
        "",
    ),
    "the wake-up is in the past": ("        return max(now, min(times))", "        return min(times)"),
    "an empty gateway wakes up": (
        "        if not times:\n            return None\n        return max(now, min(times))",
        "        return max(now, min(times)) if times else now",
    ),
    "dispatched calls wake the gateway": (
        "            if pending.dispatched is not None:\n                continue\n            state = self.providers[pending.call.provider]\n            minimum",
        "            state = self.providers[pending.call.provider]\n            minimum",
    ),
    # recovery
    "recovery finds nothing": (
        '        for entry in self.journal.entries():\n            capture_id = entry["capture_id"]\n            if capture_id in self.store:\n                continue',
        '        for entry in []:\n            capture_id = entry["capture_id"]\n            if capture_id in self.store:\n                continue',
    ),
    "recovery repeats itself": (
        "            if capture_id in self.store:\n                continue\n            sent_at",
        "            sent_at",
    ),
    "recovery marks finished calls abandoned": (
        "            if capture_id in self.store:\n                continue\n            sent_at",
        "            if False:\n                continue\n            sent_at",
    ),
    "abandoned calls are not marked": ('"outcome": "ABANDONED"}', '"outcome": "UNKNOWN"}'),
    "abandoned calls keep a status": (
        'RawCapture(capture_id, entry["provider"], request, None, {}, b"", sent_at, max(now, sent_at), None)',
        'RawCapture(capture_id, entry["provider"], request, 200, {}, b"", sent_at, max(now, sent_at), None)',
    ),
    "abandoned calls are not charged": ('                state.governor.charge(now, float(entry["cost"]))', "                pass"),
    "old abandoned calls are charged": (
        "            if state is not None and (now - sent_at).total_seconds() <= state.governor.window_s:",
        "            if state is not None:",
    ),
    "abandoned calls are charged the wrong cost": ('state.governor.charge(now, float(entry["cost"]))', "state.governor.charge(now, 1.0)"),
    "recovery does not report the abandoned": ("            abandoned.append(capture_id)\n", ""),
    "recovery of an unknown provider crashes": (
        '            state = self.providers.get(entry["provider"])\n            if state is not None and',
        '            state = self.providers[entry["provider"]]\n            if state is not None and',
    ),
    "a torn journal line is read": (
        '        for raw in data[: data.rfind(b"\\n") + 1].split(b"\\n"):',
        '        for raw in data.split(b"\\n"):',
    ),
    "the journal does not remember the capture": (
        '                    "capture_id": dispatch.capture_id,',
        '                    "capture_id": "cap-" + "0" * 32,',
    ),
    "the journal keeps the arguments": (
        '                    "cost": str(cost),',
        '                    "cost": str(cost),\n                    "args": str(dict(dispatch.args)),',
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str, tmp_path: Path) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.gateway", old, new)
    try:
        found = failures(module, tmp_path)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
