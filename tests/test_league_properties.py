"""Whatever kills the process, and whenever, the league ends in a state it can be trusted from (plan unit 2.4).

The scheduler checks list the kill points one at a time. This test draws a kill for each of several hourly slots (none, or the process dying
at one of the four places a cycle can be cut), lets a new process run the tick again after each, and asks the questions that must hold at
the end of any such history, not of one: no cycle is left open, a cycle has all of its forecasts or none, no forecast that was committed
is lost, nothing is left queued, and every record of every cycle and slot still reads as a valid history.
"""

from __future__ import annotations

import contextlib
import dataclasses
import tempfile
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import cycle, league, lifecycle, practice, scheduler
from hoshitori.timeline import from_ms
from tests import league_checks as lc
from tests import scheduler_checks as sc
from tests.commit_checks import ledger_in
from tests.recovery_checks import Died

KILLS = ("none", "before_batch_append", "after_group_journaled", "before_group_journaled", "batch_never_written")


def _arm(w: Any, kind: str) -> None:
    """Make the next tick of this world die at the place ``kind`` names."""
    if kind == "before_batch_append":
        sc.die_before_batch_append(w)
    elif kind == "after_group_journaled":
        sc.die_after_group_journaled(w)
    elif kind == "before_group_journaled":
        sc.die_before_group_journaled(w)
    elif kind == "batch_never_written":
        real = w.ledger.submit_many

        def refuse(records: Any) -> Any:
            if any(r.get("type") == "snapshot" for r in records):
                raise Died
            return real(records)

        w.ledger.submit_many = refuse


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(KILLS), min_size=1, max_size=3), st.integers(min_value=0, max_value=12), st.booleans())
def test_any_history_of_kills_ends_in_a_trustworthy_ledger(kills: list[str], minutes_late: int, reopen: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        w = sc.world(root)
        cfg = sc.config(scheduler, root)
        services = w.services(league)
        real_append, real_many = w.ledger.append_pending, w.ledger.submit_many
        for index, kind in enumerate(kills):
            start = lc.SLOT + index * lc.HOUR
            w.clock.set(from_ms(start + 20_000))
            _arm(w, kind)
            with contextlib.suppress(Died):
                scheduler.tick(services, cfg)
            w.ledger.append_pending, w.ledger.submit_many = real_append, real_many  # a new process: the patches are gone
            if reopen:  # and, in this history, a process that opens the ledger from its files: the index, the queue and the checkpoints
                w.ledger.close()
                w.ledger = ledger_in(root, w.ledger_clock)
                services = dataclasses.replace(services, ledger=w.ledger)
                real_append, real_many = w.ledger.append_pending, w.ledger.submit_many
            w.clock.set(from_ms(start + (15 + minutes_late) * 60_000))
            scheduler.tick(services, cfg)
        w.clock.set(from_ms(lc.SLOT + len(kills) * lc.HOUR + 20_000))
        scheduler.tick(services, cfg)  # and one more, so anything left to close is closed
        records = scheduler.read_ledger(w.ledger)
        cycles = cycle.rebuild([r for r in records if r.get("type") == cycle.TYPE])
        slots = lifecycle.rebuild([r for r in records if r.get("type") == lifecycle.TYPE])  # raises on any history that is not valid
        forecasts: dict[str, int] = {}
        made_ids: set[str] = set()
        for r in records:
            if r.get("type") == "forecast":
                cid = str(r["body"]["cycle_id"])
                forecasts[cid] = forecasts.get(cid, 0) + 1
                made_ids.add(str(r["id"]))
        try:
            assert w.ledger.pending() == 0, "something acknowledged was left queued"
            for cid, history in cycles.items():
                state = cycle.current_state(history)
                assert state in cycle.TERMINAL, f"cycle {cid} was left {state.value}"
                made = forecasts.get(cid, 0)
                assert made in (0, 6), f"cycle {cid} holds {made} forecasts: neither all nor none"
                if state is cycle.State.COMMITTED:
                    assert made == 6 or cid not in forecasts, f"a committed cycle {cid} holds {made} forecasts"
            lost = [fid for fid, h in slots.items() if h[-1].step.state.value == "FAILED_LOST" and fid in made_ids]
            assert lost == [], f"a forecast that was committed was recorded as lost: {lost}"
        finally:
            w.close()


CLOCK_MOVES = st.one_of(
    st.tuples(st.just("pass"), st.floats(min_value=0.0, max_value=600.0, allow_nan=False)),
    st.tuples(st.just("step"), st.floats(min_value=-7200.0, max_value=7200.0, allow_nan=False)),
    st.tuples(st.just("step"), st.floats(min_value=-2.5, max_value=2.5, allow_nan=False)),  # around the ledger's own limits (1 s and 2 s)
)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(st.lists(CLOCK_MOVES, min_size=1, max_size=14))
def test_a_wandering_clock_never_puts_a_record_out_of_order(moves: list[tuple[str, float]]) -> None:
    """Time passing, the wall clock stepping either way by up to two hours: whatever the host does, the records that are written carry times
    that never decrease, a refused write leaves the log as it was, and a write the clock allows is made."""
    from datetime import timedelta

    from hoshitori import canonical, ledger
    from tests.ledger_checks import START, ManualClock, open_ledger, rec

    with tempfile.TemporaryDirectory() as tmp:
        clock = ManualClock(START)
        led = open_ledger(ledger, Path(tmp), clock, records=100)
        try:
            for number, (kind, seconds) in enumerate(moves):
                (clock.advance if kind == "pass" else clock.step)(timedelta(seconds=seconds))
                before = led.size
                led.submit(rec(number))
                queued = led.pending()
                try:
                    led.append_pending()
                except ledger.ClockError:
                    assert led.size == before, "a refused write changed the log"
                    assert led.pending() == queued, "a refused write lost what was queued"
                    continue
                assert led.size == before + queued, "a write the clock allowed did not write everything that was queued"
                assert led.pending() == 0
            times = [canonical.parse_record(led.read_record(i))["committed_at"] for i in range(led.size)]
            assert times == sorted(times), f"committed_at decreased: {times}"
        finally:
            led.close()


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(st.integers(min_value=1, max_value=40), st.booleans(), st.booleans())
def test_a_kill_anywhere_in_the_settlement_pass_leaves_every_forecast_settled_once(kill_at: int, reopen: bool, practising: bool) -> None:
    """The process dies at the n-th write of the pass that settles what has matured (a settlement record, the step that follows it, whichever
    comes n-th); a new process runs the tick again, with the ledger opened from its files or not. However n falls, every forecast that
    matured has exactly one settlement, its slot is over, nothing is left queued, and the histories still read; with a practice portfolio
    configured, each book is opened exactly once."""
    from tests.settle_job_checks import Market

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        w = sc.world(root)
        cfg = sc.config(scheduler, root)
        services = w.services(league)
        w.clock.set(from_ms(lc.SLOT + 20_000))
        ran = scheduler.tick(services, cfg)
        assert ran.ran is not None
        origin_ms = ran.ran.slots[0].origin_ms
        market = Market(w, origin_ms - 60_000)
        first = w.script
        w.script = lambda call, world: first(call, world) if call.operation == "instruments" else market.script(call, world)
        services = dataclasses.replace(
            services, env=dataclasses.replace(services.env, adapters={**services.env.adapters, "okx": market.okx})
        )
        cfg = dataclasses.replace(cfg, start_grace_ms=30_000, practice=(practice.Config(),) if practising else ())
        w.clock.set(from_ms(origin_ms + lc.HOUR))
        real_many, real_append = w.ledger.submit_many, w.ledger.append_pending
        seen = {"n": 0}

        def count(real: Any) -> Any:
            def wrapped(*args: Any) -> Any:
                seen["n"] += 1
                if seen["n"] == kill_at:
                    raise Died
                return real(*args)

            return wrapped

        w.ledger.submit_many, w.ledger.append_pending = count(real_many), count(real_append)  # type: ignore[method-assign]
        with contextlib.suppress(Died):
            scheduler.tick(services, cfg)
        w.ledger.submit_many, w.ledger.append_pending = real_many, real_append  # type: ignore[method-assign]
        if reopen:
            w.ledger.close()
            w.ledger = ledger_in(root, w.ledger_clock)
            services = dataclasses.replace(services, ledger=w.ledger)
        scheduler.tick(services, cfg)
        scheduler.tick(services, cfg)
        try:
            records = scheduler.read_ledger(w.ledger)
            slots = lifecycle.rebuild([r for r in records if r.get("type") == lifecycle.TYPE])  # raises on a history that is not valid
            per_forecast: dict[str, int] = {}
            for r in records:
                if r.get("type") == "settlement":
                    fid = str(r["body"]["forecast_id"])
                    per_forecast[fid] = per_forecast.get(fid, 0) + 1
            assert all(n == 1 for n in per_forecast.values()), f"a forecast was settled more than once: {per_forecast}"
            if practising:
                books = sorted(str(r["body"]["book"]) for r in records if r.get("type") == "practice")
                assert books == ["L0:BASE", "L0:DOUBLE_FEES", "L0:STRESS"], f"the books were not opened exactly once each: {books}"
            assert w.ledger.pending() == 0, "something acknowledged was left queued"
            matured = [fid for fid, h in slots.items() if h[0].step.horizon_ms == lc.HOUR]
            assert matured, "the one-hour forecasts are not in the ledger"
            for fid in matured:
                state = slots[fid][-1].step.state
                assert state in lifecycle.TERMINAL, f"a matured forecast was left {state.value}"
                assert per_forecast.get(fid) == 1, f"a matured forecast has {per_forecast.get(fid, 0)} settlements"
        finally:
            w.close()
