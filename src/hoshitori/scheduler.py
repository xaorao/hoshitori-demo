"""The loop that keeps the league running: on every tick, close what a lost process left, run the cycle that is due, settle what
has matured.

``CYCLE_RUNNER_SPEC`` section 4 is the rule. The state is the ledger: a tick reads the records, so a new process continues
from exactly where the last one stopped, and nothing else is remembered. In order:

1. **Recovery.** A cycle that is not over when a tick starts was left by a process that is gone: it is closed as abandoned
and its undecided slots are failed as lost (``recovery``). Committed forecasts are not touched. 2. **The due cycle.** The
latest slot of the schedule that has come, has no cycle yet and is not older than the start grace. A slot that passed while
nothing ran is not made up later: the heartbeat reports it as missing, and the next slot runs on its own time. Before the
cycle the registry is refreshed from the venues' lists (the records go to the ledger), and what the cycle committed is kept
for a bundle. 3. **Settlement.** Every forecast that is waiting for its origin, open or settling moves as far as the clock
allows (``settle_job``). 4. **The heartbeat.** From the schedule and the cycle records alone: slots that never started, never
finished, finished late or were abandoned.

A defect in the cycle is raised to the caller after the cycle has been closed as far as the ledger allowed; the loop records
it and goes on to the next tick, which will not run that slot again (it has a cycle now).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hoshitori import (
    canonical,
    collect,
    commitment_store,
    cycle,
    desk_hook,
    forecast,
    league,
    lifecycle,
    post_snapshot_job,
    practice,
    practice_job,
    recovery,
    registry_job,
    ryo_plan_job,
    ryo_read,
    settle_job,
    settlement_record,
)
from hoshitori.evidence import record_digest
from hoshitori.ledger import LedgerView
from hoshitori.registry_job import ListSpec, Refresh


class SchedulerError(ValueError):
    """A configuration that cannot run."""


@dataclass(frozen=True)
class Config:
    start_grace_ms: int  # a slot is run only within this long after its time (less than the interval between slots)
    finish_within_ms: int  # the heartbeat calls a cycle late that was committed after this long
    lists: tuple[ListSpec, ...]  # the instrument lists to refresh before a cycle
    store: Path | None = None  # where the commitments are kept for bundles; none: not kept
    wait_s: float = 30.0
    practice: tuple[practice.Config, ...] = ()  # the practice portfolios (one per arm) run beside the settlement pass; none: no practice
    on_demand: Callable[[], settle_job.Passed] | None = (
        None  # the settlement pass of the on-demand ledger, run after the league's own; none: none
    )
    desk: Callable[[list[dict[str, Any]], str, int], desk_hook.Passed] | None = (
        None  # the Desk on the events of a cycle just committed that then moved (``desk_hook``), under its daily cap; none: not run
    )


@dataclass(frozen=True)
class Tick:
    now_ms: int
    closed: tuple[recovery.Closed, ...]
    refreshed: Refresh | None
    ran: league.Outcome | None
    passed: settle_job.Passed | None
    findings: tuple[cycle.Beat, ...]
    practiced: tuple[practice_job.Passed, ...] | None = None  # one per arm; none when the configuration has no practice
    on_demand: settle_job.Passed | None = None  # the on-demand ledger's settlement pass; none when there is none or it failed
    on_demand_failed: str | None = None  # why it failed: the pass never stops the league's tick, and a failure is said, not hidden
    planned: ryo_plan_job.Passed | None = None  # arm R's plans settled in this tick; none when the ledger holds no read of RYO
    desked: desk_hook.Passed | None = None  # the Desk's runs on this tick's cycle; none when no Desk is configured or no cycle committed
    desk_failed: str | None = None  # why the Desk step failed: like the on-demand pass, it never stops the league's tick


def read_ledger(ledger: LedgerView) -> list[dict[str, Any]]:
    """Every record of a ledger, in order."""
    return [canonical.parse_record(ledger.read_record(seq)) for seq in range(ledger.size)]


def read_all(sv: league.Services) -> list[dict[str, Any]]:
    """Every record of the ledger a league writes to, in order."""
    return read_ledger(sv.ledger)


def check_config(sv: league.Services, config: Config) -> None:
    every = sv.protocol.schedule_every_ms
    if isinstance(config.start_grace_ms, bool) or not isinstance(config.start_grace_ms, int) or not 0 <= config.start_grace_ms < every:
        raise SchedulerError("the start grace is a whole number of milliseconds, less than the interval between slots")
    if isinstance(config.finish_within_ms, bool) or not isinstance(config.finish_within_ms, int) or config.finish_within_ms < 1:
        raise SchedulerError("the time a cycle has to finish is a positive number of milliseconds")
    if isinstance(config.wait_s, bool) or not isinstance(config.wait_s, int | float) or config.wait_s <= 0:
        raise SchedulerError("the wait is a positive number of seconds")
    if not config.lists:
        raise SchedulerError("there is at least one instrument list to refresh")


def due_slot(sv: league.Services, histories: dict[str, cycle.History], now_ms: int, grace_ms: int) -> int | None:
    """The latest slot that has come, is within the grace, and has no cycle yet."""
    started = {h[0].step.planned_for_ms for h in histories.values()}
    slots = [s for s in sv.protocol.slots_between(now_ms - grace_ms - 1, now_ms) if s not in started]
    return max(slots) if slots else None


def since_of(sv: league.Services, histories: dict[str, cycle.History], slot_ms: int) -> int:
    """When the registry was last read for changes: the latest discovery of any cycle, else one interval before the slot."""
    found = [cast(int, t.step.discovery_at_ms) for h in histories.values() for t in h if t.step.state is cycle.State.DISCOVERING]
    return min(max(found), slot_ms) if found else max(slot_ms - sv.protocol.schedule_every_ms, 0)


def waiting(records: list[dict[str, Any]]) -> list[settle_job.Waiting]:
    """The forecasts that have not been settled, with their records' digests and their histories. A slot still in play also gets the
    settlement record the ledger holds for it, if a pass wrote one and stopped before the step that ends the slot: the pass ends the slot
    with that record and never settles it again (``settle_job``). A forecast record's id is its forecast id, which a settlement names."""
    histories = lifecycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == lifecycle.TYPE]))
    settled = {r["body"]["forecast_id"]: r for r in records if r.get("type") == settlement_record.TYPE}
    live = (lifecycle.State.AWAITING_ORIGIN, lifecycle.State.OPEN, lifecycle.State.SETTLING)
    out = []
    for record in records:
        history = histories.get(record["id"]) if record.get("type") == forecast.TYPE else None
        if history is None or lifecycle.current_state(history) not in live:
            continue
        clean = {k: v for k, v in record.items() if k != "committed_at"}
        read = forecast.from_record(clean)
        out.append(settle_job.Waiting(read, record_digest(clean), history, settled.get(record["id"])))
    return out


def tick(sv: league.Services, config: Config) -> Tick:
    """One pass of the loop."""
    check_config(sv, config)
    protocol = sv.protocol
    now = collect.now_ms(sv.env.clock)
    # An acknowledged command is in the ledger or ends as FAILED_LOST (ADR-027). A process that was killed after it acknowledged a batch and
    # before it appended it left those records queued: they are appended first, so that recovery reads what the log holds and no later step
    # writes a record that conflicts with a queued one (which, left queued, would fail every tick for ever).
    sv.ledger.append_pending()
    records = read_all(sv)
    ended, closed = recovery.close_lost(records, protocol, sv.source_urn, now, sv.rejudge)
    league.put(sv.ledger, list(ended))
    records = read_all(sv) if ended else records
    histories = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == cycle.TYPE]))
    slot = due_slot(sv, histories, now, config.start_grace_ms)
    refreshed: Refresh | None = None
    ran: league.Outcome | None = None
    if slot is not None:
        refreshed = registry_job.refresh(sv.env, config.lists, wait_s=config.wait_s)
        league.put(sv.ledger, [registry_job.ledger_record(r, sv.source_urn) for r in refreshed.records])
        ran = league.run_cycle(sv, slot, since_ms=since_of(sv, histories, slot))
        if ran.commitment is not None and config.store is not None:
            commitment_store.save(config.store, ran.commitment)
        records = read_all(sv)
    pending = waiting(records)
    passed: settle_job.Passed | None = None
    if pending:
        passed = settle_job.settle_pass(settle_job.SettleServices(protocol, sv.env, sv.source_urn), pending, wait_s=config.wait_s)
        league.put(sv.ledger, list(passed.records))
    planned: ryo_plan_job.Passed | None = None
    if any(r.get("type") == ryo_read.TYPE for r in records):  # RYO's plans settle on the windows just settled, by the same rules
        planned = ryo_plan_job.plan_pass(
            settle_job.SettleServices(protocol, sv.env, sv.source_urn), read_all(sv), collect.now_ms(sv.env.clock), wait_s=config.wait_s
        )
        league.put(sv.ledger, list(planned.records))
    od_passed: settle_job.Passed | None = None
    od_failed: str | None = None
    if config.on_demand is not None:
        # The separate ledger of forecasts nobody scheduled settles on the same clock. Its pass is idempotent (it writes only what is new),
        # and a failure in it (a venue down, a ledger refusing a record) is reported on the tick and never stops the league's own work.
        try:
            od_passed = config.on_demand()
        except Exception as exc:
            od_failed = f"{type(exc).__name__}: {exc}"[:300]
    desked: desk_hook.Passed | None = None
    desk_failed: str | None = None
    if config.desk is not None and ran is not None and ran.commitment is not None:
        try:
            desked = config.desk(read_all(sv), ran.cycle_id, collect.now_ms(sv.env.clock))
        except Exception as exc:
            desk_failed = f"{type(exc).__name__}: {exc}"[:300]
    # the registry's changes after a sealed snapshot are events of their own and revise only the theses they concern (idempotent)
    # (read the clock again: the registry was refreshed during this tick, after `now` was taken, and its changes count)
    seen_at = collect.now_ms(sv.env.clock)
    post_snapshot_job.post_snapshot_pass(sv.ledger, sv.env.registry, now_ms=seen_at, source=sv.source_urn)
    practiced: tuple[practice_job.Passed, ...] | None = None
    if config.practice:
        practiced = practice_job.practice_pass(
            settle_job.SettleServices(protocol, sv.env, sv.source_urn), config.practice, read_all(sv), now, wait_s=config.wait_s
        )
        league.put(sv.ledger, [record for each in practiced for record in each.records])
    histories = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == cycle.TYPE]))
    schedule = protocol.slots_through(now)
    beats = cycle.heartbeat(
        schedule, histories.values(), now_ms=now, start_grace_ms=config.start_grace_ms, finish_within_ms=config.finish_within_ms
    )
    return Tick(now, closed, refreshed, ran, passed, beats, practiced, od_passed, od_failed, planned, desked, desk_failed)


def run_forever(
    sv: league.Services,
    config: Config,
    stop: threading.Event,
    *,
    every_s: float,
    report: Callable[[Tick | Exception], None],
) -> None:
    """Tick until told to stop; a tick that fails is reported and the loop goes on."""
    if isinstance(every_s, bool) or not isinstance(every_s, int | float) or every_s <= 0:
        raise SchedulerError("the pause between ticks is a positive number of seconds")
    while not stop.is_set():
        try:
            report(tick(sv, config))
        except Exception as exc:
            report(exc)
        stop.wait(every_s)
