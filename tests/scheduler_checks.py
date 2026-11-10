"""Checks of the scheduler (``hoshitori.scheduler``), runnable against a mutant.

Everything below the loop is real: the registry and its refresh through a scripted exchange, the cycle, the commitment against a real ledger,
the recovery and the settlement. The clock is moved by hand; a process that dies leaves the ledger as it was.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import tempfile
import threading
import time
import types
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import commitment_store, cycle, league, lifecycle, rejudge
from hoshitori.clock import ManualClock
from hoshitori.evidence import record_digest
from hoshitori.gateway import Call
from hoshitori.registry_job import ListSpec
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import parse_rfc3339
from tests.collect_checks import Reply
from tests.league_checks import HOUR, NOW, SLOT, URN, World, histories, states
from tests.recovery_checks import Died, dying_attestor
from tests.registry_checks import inst, lst
from tests.registry_job_checks import spot_body

MINUTE = 60_000
GRACE = 10 * MINUTE
FINISH = 20 * MINUTE


def script(call: Call, world: World) -> Reply:
    if call.operation == "instruments":
        return Reply(spot_body("BTC", "ETH", "SOL"))
    return world.default(call)


def config(mod: types.ModuleType, root: Path | None = None, **over: Any) -> Any:
    values: dict[str, Any] = {
        "start_grace_ms": GRACE,
        "finish_within_ms": FINISH,
        "lists": (ListSpec("binance-spot", "spot"),),
        "store": None if root is None else root / "commitments",
    }
    values.update(over)
    return mod.Config(**values)


def world(root: Path, **kw: Any) -> World:
    return World(root, script=kw.pop("script", script), **kw)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.SchedulerError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for cls in (mod.Config, mod.Tick):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_a_tick(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    sv, cfg = w.services(league), config(mod, root)
    first = mod.tick(sv, cfg)
    c.eq(
        "the slot that has come is run",
        (first.ran is not None and first.ran.state.value, first.now_ms, first.closed),
        ("COMMITTED", to_ms(NOW), ()),
    )
    assert first.ran is not None and first.ran.commitment is not None
    c.eq(
        "the registry was refreshed first, and its record is in the ledger",
        ([r["body"]["events"][-1]["venue"] for r in w.records("registry")], first.refreshed is not None and len(first.refreshed.records)),
        (["binance-spot"], 1),
    )
    c.eq(
        "the commitment is kept for a bundle",
        sorted(p.name for p in (root / "commitments").iterdir()),
        [first.ran.commitment.commitment_id],
    )
    saved = commitment_store.load(root / "commitments", first.ran.commitment.commitment_id)
    c.eq("with its note and tokens", (saved.note, saved.tokens), (first.ran.commitment.note, first.ran.commitment.attestation.tokens()))
    c.eq(
        "forecasts wait for their origin: the pass over them moves nothing",
        (first.passed is not None and first.passed.records, first.findings),
        (None, ()) if first.passed is None else ((), ()),
    )
    second = mod.tick(sv, cfg)
    c.eq("the same slot is not run again", (second.ran, second.refreshed), (None, None))
    c.eq("nothing is lost and nothing is late", (second.closed, second.findings), ((), ()))
    c.eq(
        "a forecast that waits is looked at on every tick",
        second.passed is not None and (second.passed.records, second.passed.pending),
        ((), ()),
    )
    c.eq("one cycle in the ledger", len(w.records("cycle_state")), 8)
    c.eq("no change after the snapshot, no event", w.records("post_snapshot_event"), [])
    w.registry.admit(
        lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in ("ETH", "SOL")]),
        scope="spot",
        known_at=from_ms(to_ms(NOW) + 2 * MINUTE),
        capture_id="cap-later",
    )
    w.clock.set(from_ms(to_ms(NOW) + 5 * MINUTE))
    third = mod.tick(sv, cfg)
    events = w.records("post_snapshot_event")
    c.eq(
        "an instrument that left a venue after the snapshot is recorded as an event on the tick that sees it",
        ([e["body"]["kind"] for e in events], third.ran),
        (["REGISTRY_INSTRUMENT_REMOVED"], None),
    )
    c.eq("and revises the theses that settle on it", len(w.records("thesis_revision")) > 0, True)
    mod.tick(sv, cfg)
    c.eq(
        "the next tick adds nothing",
        (len(w.records("post_snapshot_event")), len(w.records("thesis_revision"))),
        (1, len(w.records("thesis_revision"))),
    )
    w.close()
    # the store may be left out
    bare = world(root / "bare")
    mod.tick(bare.services(league), config(mod, None))
    c.eq("without a store nothing is written", sorted(p.name for p in (root / "bare").iterdir() if p.name == "commitments"), [])
    bare.close()
    return c.bad


def _skewed(world: World) -> bytes:
    return f'{{"serverTime":{to_ms(world.clock.now()) + 60_000}}}'.encode()


def check_change_seen_during_the_tick(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    gone = {"yes": False}

    def moving(call: Call, world: World) -> Reply:
        if call.operation == "instruments" and gone["yes"]:
            world.clock.advance(timedelta(seconds=20))  # the venue answers a moment after the tick began
            return Reply(spot_body("ETH", "SOL"))
        return script(call, world)

    w = world(root, script=moving)
    sv, cfg = w.services(league), config(mod, root)
    mod.tick(sv, cfg)
    gone["yes"] = True
    w.clock.set(from_ms(SLOT + HOUR + 30_000))
    mod.tick(sv, cfg)
    c.eq(
        "a change the registry saw while this tick refreshed it is recorded on this tick",
        [(e["body"]["kind"], e["body"]["venue"]) for e in w.records("post_snapshot_event")],
        [("REGISTRY_INSTRUMENT_REMOVED", "binance-spot")],
    )
    c.eq("and the open calls that settle on it are revised", len(w.records("thesis_revision")) > 0, True)
    w.close()
    return c.bad


def check_on_demand_pass(mod: types.ModuleType, root: Path) -> list[str]:
    """The on-demand ledger's settlement runs on the league's tick: once, idempotently, and a failure in it never stops the tick."""
    from hoshitori import ondemand, settle_job
    from tests.commit_checks import RULES, attestor, ledger_in
    from tests.ondemand_checks import _world_context, long
    from tests.settle_job_checks import Rig

    c = Case(mod)
    rig = Rig(root / "rig")
    now = to_ms(rig.w.clock.now())
    od = ledger_in(root / "od", rig.w.clock)
    sub = ondemand.submission_from_desk(long(), "run-a", _world_context(ondemand, rig, "BTC", now), committed_at_ms=now)
    made = ondemand.commit(
        sub, ledger=od, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=now, registry=rig.w.registry
    )
    rig.market.reference_open = made.origin_ms - MINUTE
    settling = rig.services(settle_job)
    sv = rig.w.services(league)

    def pass_() -> Any:
        return ondemand.settle(settling, od)

    def kinds(kind: str) -> int:
        return sum(
            1
            for seq in range(od.size)
            if json.loads(od.read_record(seq))["type"] == kind  # type: ignore[arg-type]
        )

    bare = mod.tick(sv, config(mod, root / "bare"))
    c.eq("without a pass nothing is reported", (bare.on_demand, bare.on_demand_failed), (None, None))
    cfg = config(mod, root / "with", on_demand=pass_)
    rig.at(made.origin_ms + HOUR // 2)
    early = mod.tick(sv, cfg)
    c.eq(
        "before the horizon the pass runs and settles nothing",
        (early.on_demand is not None, early.on_demand_failed, kinds("settlement")),
        (True, None, 0),
    )
    rig.at(made.origin_ms + HOUR + 5 * MINUTE)
    late = mod.tick(sv, cfg)
    c.eq(
        "after the horizon the tick settles the forecast on the on-demand ledger",
        (late.on_demand is not None and len(late.on_demand.records) > 0, late.on_demand_failed, kinds("settlement")),
        (True, None, 1),
    )
    size = od.size
    again = mod.tick(sv, cfg)
    c.eq("the next tick writes nothing to it", (again.on_demand is not None and again.on_demand.records, od.size), ((), size))

    def boom() -> Any:
        raise RuntimeError("the venue was down")

    failing = mod.tick(sv, config(mod, root / "boom", on_demand=boom))
    c.eq(
        "a failure in the pass is said on the tick and does not stop it",
        (failing.on_demand, failing.on_demand_failed, failing.now_ms > 0),
        (None, "RuntimeError: the venue was down", True),
    )

    def long_boom() -> Any:
        raise RuntimeError("x" * 500)

    cut = mod.tick(sv, config(mod, root / "long", on_demand=long_boom))
    c.eq("a long reason is cut to three hundred characters", cut.on_demand_failed, ("RuntimeError: " + "x" * 500)[:300])
    od.close()
    rig.w.close()
    return c.bad


def check_abandoned_tick(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root, script=lambda call, world: Reply(_skewed(world)) if call.operation == "time" else script(call, world))
    out = mod.tick(w.services(league), config(mod, root))
    c.eq(
        "a cycle abandoned in this tick is in this tick's heartbeat",
        (out.ran is not None and out.ran.state.value, [(b.finding.value, b.planned_for_ms) for b in out.findings]),
        ("ABANDONED", [("ABANDONED", SLOT)]),
    )
    w.close()
    return c.bad


def check_missed_slots(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root, clock=ManualClock(from_ms(SLOT + 2 * HOUR + 30 * MINUTE)))  # the process was down for two and a half hours
    sv, cfg = w.services(league), config(mod, root)
    late = mod.tick(sv, cfg)
    c.eq("a slot older than the grace is not made up", (late.ran, late.refreshed), (None, None))
    c.eq(
        "the heartbeat says which slots never began",
        [(b.planned_for_ms, b.finding.value) for b in late.findings],
        [(SLOT + k * HOUR, "MISSING") for k in (0, 1, 2)],
    )
    w.clock.set(from_ms(SLOT + 3 * HOUR + 30_000))
    now = mod.tick(sv, cfg)
    assert now.ran is not None
    c.eq(
        "the slot of the hour runs on its own time",
        (now.ran.state.value, [t.step.planned_for_ms for t in now.ran.history[:1]]),
        ("COMMITTED", [SLOT + 3 * HOUR]),
    )
    c.eq(
        "and the missed ones stay missed",
        [(b.planned_for_ms, b.finding.value) for b in now.findings],
        [(SLOT + k * HOUR, "MISSING") for k in (0, 1, 2)],
    )
    w.clock.set(from_ms(SLOT + 3 * HOUR + GRACE))
    c.eq(
        "exactly at the end of the grace, a slot that has no cycle is still run",
        mod.due_slot(sv, {}, SLOT + 3 * HOUR + GRACE, GRACE),
        SLOT + 3 * HOUR,
    )
    c.eq("one millisecond later it is not", mod.due_slot(sv, {}, SLOT + 3 * HOUR + GRACE + 1, GRACE), None)
    w.close()
    return c.bad


def check_pure_parts(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    sv = w.services(league)
    c.eq("before the first slot nothing is due", mod.due_slot(sv, {}, SLOT - 1, GRACE), None)
    c.eq("at the first slot it is", mod.due_slot(sv, {}, SLOT, GRACE), SLOT)
    c.eq(
        "with the grace of zero, only at the very moment", (mod.due_slot(sv, {}, SLOT, 0), mod.due_slot(sv, {}, SLOT + 1, 0)), (SLOT, None)
    )
    c.eq(
        "the latest of the slots that are within the grace",
        mod.due_slot(dataclasses.replace(sv, protocol=_dense(w)), {}, SLOT + 5 * MINUTE + 1, 6 * MINUTE),
        SLOT + 5 * MINUTE,
    )
    started = {"x": cycle.begin(w.protocol.sha256, SLOT, SLOT)}
    c.eq("a slot that has a cycle is not due", mod.due_slot(sv, started, SLOT + 1, GRACE), None)
    c.eq("with no cycle before, the registry was last read an interval before the slot", mod.since_of(sv, {}, SLOT + HOUR), SLOT)
    c.eq("or at the start of time if the slot is the first", mod.since_of(sv, {}, 1000), 0)
    h = cycle.advance(cycle.begin(w.protocol.sha256, SLOT, SLOT), cycle.Step(cycle.State.DISCOVERING, SLOT + 5, discovery_at_ms=SLOT + 3))
    c.eq("after a cycle, the latest discovery", mod.since_of(sv, {"x": h}, SLOT + HOUR), SLOT + 3)
    h2 = cycle.advance(
        cycle.begin(w.protocol.sha256, SLOT + HOUR, SLOT + HOUR),
        cycle.Step(cycle.State.DISCOVERING, SLOT + HOUR + 9, discovery_at_ms=SLOT + HOUR + 7),
    )
    c.eq("of several cycles, the latest discovery", mod.since_of(sv, {"x": h, "y": h2}, SLOT + 2 * HOUR), SLOT + HOUR + 7)
    c.eq("whatever order they are in", mod.since_of(sv, {"y": h2, "x": h}, SLOT + 2 * HOUR), SLOT + HOUR + 7)
    c.eq("never later than the slot", mod.since_of(sv, {"x": h}, SLOT + 1), SLOT + 1)
    c.eq(
        "a cycle that never reached discovery says nothing",
        mod.since_of(sv, {"x": cycle.begin(w.protocol.sha256, SLOT, SLOT)}, SLOT + HOUR),
        SLOT,
    )
    c.eq("nothing waits in an empty ledger", mod.waiting([]), [])
    w.run(league)
    everything = w.records()
    records = [r for r in everything if r["type"] == "forecast"]
    c.eq("a forecast record without any step of its slot is not waiting", mod.waiting(records), [])
    pending = mod.waiting(everything)
    c.eq("six forecasts wait, each with its history", (len(pending), {states(p.history)[-1] for p in pending}), (6, {"AWAITING_ORIGIN"}))
    c.eq(
        "the digest is the digest of the record as a producer submitted it",
        sorted(p.digest for p in pending),
        sorted(record_digest({k: v for k, v in r.items() if k != "committed_at"}) for r in records),
    )
    c.eq("without the steps of the slots nothing waits", mod.waiting([r for r in everything if r["type"] != "forecast_state"]), [])
    w.close()
    from tests.league_checks import barrier_map, barrier_world

    bm = barrier_map()
    bw = barrier_world(root / "barrier", bm)
    bw.run(league, barrier_mappings={"L0": bm})
    kept = mod.waiting(bw.records())
    c.eq(
        "barrier forecasts wait beside the directional ones",
        sorted((p.forecast.common.contract, p.forecast.common.horizon_ms // HOUR) for p in kept),
        sorted((k, h) for k in "BD" for h in (1, 4, 24) for _ in (0, 1)),
    )
    bw.close()
    return c.bad


def _dense(w: World) -> Any:
    from hoshitori import protocol
    from tests.protocol_checks import make

    return make(
        protocol, primary_venue="binance-spot", second_venue="okx", schedule_start_ms=SLOT, schedule_every_ms=MINUTE, arms=w.protocol.arms
    )


def check_recovery_and_settlement(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root, attestor=dying_attestor)
    sv, cfg = w.services(league), config(mod, root)
    with contextlib.suppress(Died):
        mod.tick(sv, cfg)
    cycles, slots = histories(w)
    c.eq(
        "the first process died in the middle of the cycle",
        ({states(h)[-1] for h in cycles.values()}, {states(h)[-1] for h in slots.values()}),
        ({"COMMITTING"}, {"DECIDED"}),
    )
    from tests.commit_checks import attestor
    from tests.league_checks import GOOD

    w.clock.set(from_ms(SLOT + 15 * MINUTE))
    healthy = dataclasses.replace(sv, attestor=attestor(GOOD))
    after = mod.tick(healthy, cfg)
    c.eq(
        "a new process finds the lost cycle, whose forecasts are in the log, and rolls its slots forward",
        [(x.state_was.value, x.slots_failed, x.slots_rolled) for x in after.closed],
        [("COMMITTING", 0, 6)],
    )
    c.eq("and does not run the slot again", after.ran, None)
    cycles, slots = histories(w)
    c.eq(
        "the ledger says so: the cycle is committed and its slots have gone on, not been failed as lost",
        ({states(h)[-1] for h in cycles.values()}, {states(h)[-1] for h in slots.values()}),
        ({"COMMITTED"}, {"OPEN"}),
    )
    c.eq("nothing is reported as abandoned", [b.finding.value for b in after.findings], [])
    # the next hour's slot runs, and later its forecasts are settled
    w.clock.set(from_ms(SLOT + HOUR + 20_000))
    ran = mod.tick(healthy, cfg)
    assert ran.ran is not None and ran.ran.commitment is not None
    c.eq("the next slot runs normally", ran.ran.state.value, "COMMITTED")
    return c.bad + _settle(mod, w, healthy, cfg, ran)


def check_recovery_uses_the_stamps(mod: types.ModuleType, root: Path) -> list[str]:
    """The service's judge reaches recovery: a cycle killed after its batch was stamped is rolled forward at the level the kept time-stamps
    support and names the commitment the judge found, and without a judge it is C1 as before."""
    from tests.commit_checks import RULES, attestor
    from tests.league_checks import GOOD

    c = Case(mod)
    w = world(root, attestor=dying_attestor)
    sv, cfg = w.services(league), config(mod, root)
    with contextlib.suppress(Died):
        mod.tick(sv, cfg)
    asked: list[list[str]] = []

    def judge(records: list[dict[str, Any]]) -> Any:
        asked.append(sorted(str(r["id"]) for r in records))
        committed = max(to_ms(parse_rfc3339(str(r["committed_at"]))) for r in records)
        return rejudge.Judged(attestor(GOOD)(bytes(range(32)), committed), RULES, "cmfound")

    w.clock.set(from_ms(SLOT + 15 * MINUTE))
    healthy = dataclasses.replace(sv, attestor=attestor(GOOD), rejudge=judge)
    after = mod.tick(healthy, cfg)
    c.eq("recovery rolled the six forward", [(x.slots_failed, x.slots_rolled) for x in after.closed], [(0, 6)])
    c.eq("the judge was asked once, with the six forecasts", [len(x) for x in asked], [6])
    cycles, slots = histories(w)
    final = next(iter(cycles.values()))[-1].step
    c.eq("the cycle names the commitment the judge found", (final.state.value, final.commitment_id), ("COMMITTED", "cmfound"))
    levels = {next(t.step.level.value for t in h if t.step.state is lifecycle.State.COMMITTED) for h in slots.values()}
    c.eq("and its slots are at the level the stamps support", levels, {"C3"})
    return c.bad


def _settle(mod: types.ModuleType, w: World, sv: Any, cfg: Any, ran: Any) -> list[str]:
    from tests.settle_job_checks import Market

    c = Case(mod)
    origin_ms = ran.ran.slots[0].origin_ms
    market = Market(w, origin_ms - MINUTE)
    first = w.script

    def both(call: Call, world: World) -> Reply:
        return first(call, world) if call.operation == "instruments" else market.script(call, world)

    w.script = both
    w.clock.set(from_ms(origin_ms + HOUR))
    sv = dataclasses.replace(sv, env=dataclasses.replace(sv.env, adapters={**sv.env.adapters, "okx": market.okx}))
    done = mod.tick(sv, dataclasses.replace(cfg, start_grace_ms=30_000))  # the next slot has gone by a minute: it is not made up
    c.eq("a tick settles what has matured", (done.passed is not None and len(done.passed.records) > 0, done.ran), (True, None))
    settled = sorted((x["body"]["state"], x["body"].get("reason")) for x in w.records("settlement"))
    # the two of the next slot settle on its market; the two of the cycle that was rolled forward after the kill reach settlement too,
    # and tie here because the world's market is flat in their window
    c.eq(
        "the one-hour forecasts of both cycles are settled",
        settled,
        [("SETTLED", None), ("SETTLED", None), ("VOID", "VOID_TIE"), ("VOID", "VOID_TIE")],
    )
    ends = {fid: states(h)[-1] for fid, h in histories(w)[1].items()}
    c.eq("the others of both cycles wait", sorted(ends.values()).count("OPEN"), 8)
    from hoshitori import practice

    c.eq("without a practice configuration there is no practice pass", (done.practiced, w.records("practice")), (None, []))
    practising = mod.tick(sv, dataclasses.replace(cfg, start_grace_ms=30_000, practice=(practice.Config(),)))
    books = sorted(x["body"]["book"] for x in w.records("practice"))
    c.eq(
        "with one the books are opened and written to the ledger",
        (practising.practiced is not None and [x.arm for x in practising.practiced], books),
        (["L0"], ["L0:BASE", "L0:DOUBLE_FEES", "L0:STRESS"]),
    )
    return c.bad


def die_before_batch_append(w: World) -> None:
    """The process is killed after the batch's records were submitted (acknowledged, queued) and before they were appended."""
    real = w.ledger.append_pending

    def append_pending() -> int:
        if w.ledger.pending() >= 10:  # the batch: every earlier put appends one or two records
            raise Died
        return real()

    w.ledger.append_pending = append_pending  # type: ignore[method-assign]


def _after_batch(record: Any) -> bool:
    return bool(record.get("type") == "forecast_state" and record["body"].get("state") == "COMMITTED")


def die_after_group_journaled(w: World) -> None:
    """The process is killed just after the steps that follow the batch were acknowledged (journaled and queued) and before they were
    appended: the group is whole in the queue."""
    real = w.ledger.submit_many

    def submit_many(records: Any) -> Any:
        made = real(records)
        if any(_after_batch(r) for r in records):
            raise Died
        return made

    w.ledger.submit_many = submit_many  # type: ignore[method-assign]


def die_before_group_journaled(w: World) -> None:
    """The process is killed after the batch was committed and before the steps that follow it were acknowledged: the one window left."""
    real = w.ledger.submit_many

    def submit_many(records: Any) -> Any:
        if any(_after_batch(r) for r in records):
            raise Died
        return real(records)

    w.ledger.submit_many = submit_many  # type: ignore[method-assign]


def check_restart_with_queued_records(mod: types.ModuleType, root: Path) -> list[str]:
    """A process killed with acknowledged commands still queued must not stop every later tick: the queue is appended first, and then
    recovery sees what the log holds. In each of the three kills the batch of forecasts is in the log after the queue is appended, so every
    cycle ends committed with its forecasts alive (a committed forecast is never abandoned): where the steps that follow the
    batch were acknowledged together they are appended with the queue and recovery has nothing to close, and where they were not, recovery
    writes them."""
    from tests.commit_checks import ledger_in

    c = Case(mod)
    for name, arm, rolled in (
        ("after the batch was submitted", die_before_batch_append, True),
        ("after the steps that follow the batch were acknowledged", die_after_group_journaled, False),
        ("before the steps that follow the batch were acknowledged", die_before_group_journaled, True),
    ):
        directory = root / name.split()[0] / name.split()[-1]
        directory.mkdir(parents=True)
        w = world(directory)
        arm(w)
        with contextlib.suppress(Died):
            w.run(league)
        w.ledger.close(checkpoint=False)
        ledger = ledger_in(directory, ManualClock(from_ms(SLOT + 15 * MINUTE)))  # a new process opens the same directory
        try:
            services = w.services(league, ledger=ledger)
            cfg = config(mod)
            if arm is not die_before_group_journaled:
                c.eq(f"killed {name}: acknowledged commands are still queued", ledger.pending() > 0, True)
            for attempt in (1, 2):
                w.clock.set(from_ms(SLOT + (15 + attempt) * MINUTE))
                try:
                    done = mod.tick(services, cfg)
                except Exception as exc:
                    c.bad.append(f"killed {name}: tick {attempt} raised {type(exc).__name__}: {exc}")
                    break
                if attempt == 1:
                    c.eq(f"killed {name}: the queue is empty after the first tick", ledger.pending(), 0)
                    c.eq(
                        f"killed {name}: "
                        + ("recovery rolled the cycle forward" if rolled else "the steps came with the queue, nothing to close"),
                        [x.slots_rolled for x in done.closed],
                        [6] if rolled else [],
                    )
            records = mod.read_all(services)
            after = cycle.rebuild([r for r in records if r.get("type") == cycle.TYPE])
            slot_states = {states(h)[-1] for h in lifecycle.rebuild([r for r in records if r.get("type") == lifecycle.TYPE]).values()}
            c.eq(
                f"killed {name}: no cycle is left open, and it ends as it should",
                {cycle.current_state(h).value for h in after.values()},
                {"COMMITTED"},
            )
            c.eq(f"killed {name}: no forecast is lost that was committed", "FAILED_LOST" in slot_states, False)
        finally:
            ledger.close(checkpoint=False)
    return c.bad


def check_interrupted_settlement(mod: types.ModuleType, root: Path) -> list[str]:
    """A settlement pass that stopped after one slot's settlement record and before the step that ends the slot (as
    ``settle_job_checks.check_interrupted`` stops one): the next tick hands the pass the record the ledger holds, ends the slot with it as
    it is and writes nothing else for it, and settles the other slot; later ticks write nothing more for either."""
    from hoshitori import settle_job
    from tests.settle_job_checks import Market

    c = Case(mod)
    w = world(root)
    sv, cfg = w.services(league), config(mod, root)
    ran = mod.tick(sv, cfg)
    assert ran.ran is not None and ran.ran.commitment is not None
    origin_ms = ran.ran.slots[0].origin_ms
    assert origin_ms is not None
    market = Market(w, origin_ms - MINUTE)
    listing = w.script

    def both(call: Call, world: World) -> Reply:
        return listing(call, world) if call.operation == "instruments" else market.script(call, world)

    w.script = both
    sv = dataclasses.replace(sv, env=dataclasses.replace(sv.env, adapters={**sv.env.adapters, "okx": market.okx}))
    cfg = dataclasses.replace(cfg, start_grace_ms=30_000)  # the next hour's slot has gone by minutes: it is not made up
    w.clock.set(from_ms(origin_ms))
    mod.tick(sv, cfg)
    w.clock.set(from_ms(origin_ms + HOUR))
    passed = settle_job.settle_pass(settle_job.SettleServices(sv.protocol, sv.env, sv.source_urn), mod.waiting(mod.read_all(sv)))
    stop = next(i for i, rec in enumerate(passed.records) if rec["type"] == "settlement")
    league.put(sv.ledger, list(passed.records[: stop + 1]))  # the pass stops here: the slot's end and the other slot's records are lost
    written = passed.records[stop]
    fid = str(written["body"]["forecast_id"])
    one_hour = sorted(s.forecast_id for s in ran.ran.slots if s.hours == 1)
    other = next(f for f in one_hour if f != fid)
    c.eq(
        "the stopped pass left both one-hour slots settling and one settlement written",
        (
            {states(histories(w)[1][f])[-1] for f in one_hour},
            [{k: v for k, v in r.items() if k != "committed_at"} for r in w.records("settlement")],
        ),
        ({"SETTLING"}, [written]),
    )
    held = w.records("settlement")[0]
    c.eq(
        "a slot still in play is handed the settlement record the ledger holds for it, the others none",
        {p.forecast.common.forecast_id: p.settlement for p in mod.waiting(mod.read_all(sv))},
        {s.forecast_id: held if s.forecast_id == fid else None for s in ran.ran.slots},
    )
    w.clock.set(from_ms(origin_ms + HOUR + MINUTE))
    try:
        after = mod.tick(sv, cfg)
    except Exception as exc:
        c.bad.append(f"the next tick raised {type(exc).__name__}: {exc}")
        w.close()
        return c.bad
    moved = [] if after.passed is None else list(after.passed.records)
    c.eq(
        "the next tick writes the step that ends the slot and nothing else for it, and the other slot's settlement and end",
        [(rec["type"], rec["body"]["forecast_id"], rec["body"]["state"]) for rec in moved],
        [("forecast_state", fid, "SETTLED"), ("settlement", other, "SETTLED"), ("forecast_state", other, "SETTLED")],
    )
    slots = histories(w)[1]
    end = slots[fid][-1].step
    c.eq(
        "the slot ends with the record the stopped pass wrote, which stays as it was",
        (
            states(slots[fid])[-2:],
            end.settlement_id,
            end.at_ms,
            [{k: v for k, v in r.items() if k != "committed_at"} for r in w.records("settlement") if r["body"]["forecast_id"] == fid],
        ),
        (["SETTLING", "SETTLED"], written["id"], origin_ms + HOUR + MINUTE, [written]),
    )
    c.eq(
        "the other slot ends with its own settlement",
        (states(slots[other])[-1], slots[other][-1].step.settlement_id),
        ("SETTLED", next(r["id"] for r in w.records("settlement") if r["body"]["forecast_id"] == other)),
    )
    size = w.ledger.size
    w.clock.set(from_ms(origin_ms + HOUR + 2 * MINUTE))
    later = mod.tick(sv, cfg)
    c.eq(
        "a later tick writes nothing more for either",
        (
            later.passed is not None and later.passed.records,
            w.ledger.size,
            sorted(r["body"]["forecast_id"] for r in w.records("settlement")),
        ),
        ((), size, one_hour),
    )
    w.close()
    return c.bad


def check_loop_and_config(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    sv = w.services(league)
    many = "the start grace is a whole number of milliseconds, less than the interval between slots"
    c.refuses("a negative grace", lambda: mod.tick(sv, config(mod, start_grace_ms=-1)), many)
    c.refuses("a grace as long as the interval", lambda: mod.tick(sv, config(mod, start_grace_ms=HOUR)), many)
    c.refuses("a boolean grace", lambda: mod.tick(sv, config(mod, start_grace_ms=True)), many)
    c.refuses("a fractional grace", lambda: mod.tick(sv, config(mod, start_grace_ms=1.5)), many)
    mod.check_config(sv, config(mod, start_grace_ms=0))
    mod.check_config(sv, config(mod, start_grace_ms=HOUR - 1))
    fin = "the time a cycle has to finish is a positive number of milliseconds"
    c.refuses("a finish limit of zero", lambda: mod.check_config(sv, config(mod, finish_within_ms=0)), fin)
    c.refuses("a boolean finish limit", lambda: mod.check_config(sv, config(mod, finish_within_ms=True)), fin)
    mod.check_config(sv, config(mod, finish_within_ms=1))
    wait = "the wait is a positive number of seconds"
    for bad in (0, -1, True, "1"):
        c.refuses(f"a wait of {bad!r}", lambda bad=bad: mod.check_config(sv, config(mod, wait_s=bad)), wait)
    for good in (0.5, 1, 1.0, 30):
        mod.check_config(sv, config(mod, wait_s=good))
    c.refuses("no lists", lambda: mod.check_config(sv, config(mod, lists=())), "there is at least one instrument list to refresh")
    c.eq("nothing was written by refused ticks", w.records(), [])
    # the loop
    stop = threading.Event()
    seen: list[Any] = []
    broken = {"on": True}

    def flaky(call: Call, world: World) -> Reply:
        if call.operation == "instruments" and broken["on"]:
            broken["on"] = False
            raise RuntimeError("the exchange list is not reachable")
        return script(call, world)

    f = world(root / "loop", script=flaky)

    def report(item: Any) -> None:
        seen.append(item)
        if len(seen) >= 2:
            stop.set()

    mod.run_forever(f.services(league), config(mod), stop, every_s=0.01, report=report)
    c.eq("a tick that fails is reported and the loop goes on", (type(seen[0]).__name__, type(seen[1]).__name__), ("RuntimeError", "Tick"))
    c.eq("the second tick ran the slot the first could not", seen[1].ran is not None and seen[1].ran.state.value, "COMMITTED")
    pace = world(root / "pace")
    ticks: list[float] = []
    halt = threading.Event()

    def stamp(item: Any) -> None:
        ticks.append(time.monotonic())
        if len(ticks) >= 3:
            halt.set()

    mod.run_forever(pace.services(league), config(mod), halt, every_s=0.2, report=stamp)
    c.eq("the loop waits the pause between ticks", ticks[2] - ticks[0] >= 0.35, True)
    pace.close()
    done = threading.Event()
    done.set()
    mod.run_forever(f.services(league), config(mod), done, every_s=1, report=lambda item: seen.append("never"))
    c.eq("a loop told to stop does not tick", "never" in seen, False)
    for bad in (0, -1, True, "1"):
        c.refuses(
            f"a pause of {bad!r}",
            lambda bad=bad: mod.run_forever(f.services(league), config(mod), done, every_s=bad, report=lambda item: None),
            "the pause between ticks is a positive number of seconds",
        )
    f.close()
    w.close()
    return c.bad


def check_ryo_plans(mod: types.ModuleType, root: Path) -> list[str]:
    """Arm R's plans settle on the tick, after the windows they are scored on; a league that never read RYO runs no plan pass."""
    from tests.e2e_league import league

    c = Case(mod)
    with league(root / "ryo", ryo=True) as lg:
        first = mod.tick(lg.services, lg.config)
        c.eq("a tick with reads but no settled window settles no plan", first.planned is not None and first.planned.records, ())
        lg.clock.jump(62 * 60)
        mod.tick(lg.services, lg.config)
        lg.clock.jump(23 * 3600)
        day = mod.tick(lg.services, lg.config)
        written = [r for r in mod.read_all(lg.services) if r.get("type") == "ryo_plan_settlement"]
        c.eq(
            "a day on, the tick settles both plans and writes them", (len(day.planned.records) if day.planned else 0, len(written)), (2, 2)
        )
        c.eq(
            "what it returns is what it wrote",
            sorted(r["id"] for r in day.planned.records) if day.planned else [],
            sorted(r["id"] for r in written),
        )
        again = mod.tick(lg.services, lg.config)
        c.eq("the next tick writes no plan again", again.planned is not None and again.planned.records, ())
    with league(root / "plain") as lg:
        c.eq("a league that never read RYO runs no plan pass", mod.tick(lg.services, lg.config).planned, None)
    return c.bad


def check_desk_step(mod: types.ModuleType, root: Path) -> list[str]:
    """The Desk step runs after a cycle commits, on the whole ledger and that cycle; a failure in it is said on the tick and stops nothing."""
    import dataclasses

    from hoshitori import desk_hook
    from tests.e2e_league import league

    c = Case(mod)
    seen: list[tuple[int, str, int]] = []
    done = desk_hook.Passed(("t1",), (("t1", "ABSTAINED", None),))

    def step(records: list[dict[str, Any]], cycle_id: str, now_ms: int) -> desk_hook.Passed:
        seen.append((len(records), cycle_id, now_ms))
        return done

    with league(root / "desk") as lg:
        c.eq("without a Desk nothing is reported", (lambda t: (t.desked, t.desk_failed))(mod.tick(lg.services, lg.config)), (None, None))
        lg.clock.jump(3600)
        cfg = dataclasses.replace(lg.config, desk=step)
        ran = mod.tick(lg.services, cfg)
        committed = ran.ran is not None and ran.ran.commitment is not None
        c.eq("the cycle committed", committed, True)
        c.eq("the step's answer is the tick's", (ran.desked, ran.desk_failed), (done, None))
        from hoshitori.collect import now_ms as clock_ms

        after = clock_ms(lg.services.env.clock)
        got = seen[-1] if seen else (0, "", 0)
        c.eq(
            "it is given the whole ledger, the cycle and the clock's time when it runs",
            (got[0], got[1], ran.now_ms <= got[2] <= after),
            (len(mod.read_all(lg.services)), ran.ran.cycle_id if ran.ran else "", True),
        )
        calls = len(seen)
        idle = mod.tick(lg.services, cfg)
        c.eq("a tick that runs no cycle does not run the Desk", (idle.ran, idle.desked, len(seen)), (None, None, calls))

        def boom(records: list[dict[str, Any]], cycle_id: str, now_ms: int) -> desk_hook.Passed:
            raise RuntimeError("x" * 500)

        lg.clock.jump(3600)
        failing = mod.tick(lg.services, dataclasses.replace(lg.config, desk=boom))
        c.eq(
            "a failure is said, cut to three hundred characters, and the cycle still committed",
            (failing.desked, failing.desk_failed, failing.ran is not None and failing.ran.commitment is not None),
            (None, ("RuntimeError: " + "x" * 500)[:300], True),
        )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_a_tick,
        check_abandoned_tick,
        check_change_seen_during_the_tick,
        check_on_demand_pass,
        check_missed_slots,
        check_pure_parts,
        check_recovery_and_settlement,
        check_recovery_uses_the_stamps,
        check_restart_with_queued_records,
        check_interrupted_settlement,
        check_loop_and_config,
        check_ryo_plans,
        check_desk_step,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1300:]}")
    return bad


_ = (URN, NOW, lifecycle, timedelta)
