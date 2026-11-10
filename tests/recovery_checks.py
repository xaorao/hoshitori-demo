"""Checks of closing the cycles a lost process left open (``hoshitori.recovery``), runnable against a mutant.

The ledgers are real: a cycle of the league is run against a world that dies at a chosen moment (an exception that the cycle does not catch,
as a killed process would not), and what is left in the ledger is what recovery reads.
"""

from __future__ import annotations

import contextlib
import tempfile
import types
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from hoshitori import cycle, forecast, league, lifecycle, rejudge
from hoshitori import protocol as protocol_module
from hoshitori.clock import ManualClock
from hoshitori.evidence import record_digest
from hoshitori.gateway import Call, Outcome
from hoshitori.ids import natural_id
from hoshitori.protocol import Protocol
from hoshitori.timeline import origin, to_ms, void_gap
from hoshitori.timeutil import parse_rfc3339
from tests.collect_checks import Reply
from tests.commit_checks import RULES, TickClock, attestor
from tests.league_checks import GOOD, HOUR, NOW, SLOT, URN, World, histories, states
from tests.protocol_checks import make


def parse_ms(text: str) -> int:
    return to_ms(parse_rfc3339(text))


AT = 1_793_279_000_000  # a moment after the cycle's own


class Died(BaseException):
    """What a killed process leaves: nothing catches it, so nothing is cleaned up."""


def dying_attestor(digest: bytes, committed_at_ms: int) -> Any:
    raise Died


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def die_at_batch(root: Path) -> World:
    w = World(root, attestor=dying_attestor)
    with contextlib.suppress(Died):
        w.run(league)
    return w


def die_in_collection(root: Path) -> World:
    def script(call: Call, world: World) -> Reply:
        if call.operation == "candles":
            raise Died
        return world.default(call)

    w = World(root, script=script)
    with contextlib.suppress(Died):
        w.run(league)
    return w


def apply(w: World, records: Any) -> None:
    for record in records:
        w.ledger.submit(record)
    w.ledger.append_pending()


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("an hour", mod.HOUR_MS, 3_600_000)
    c.eq("the cycle's reason", mod.LOST, "PROCESS_LOST")
    c.eq("the slots' reason", mod.SLOT_REASON, "the process was lost during the cycle")
    c.eq("Closed is frozen", mod.Closed.__dataclass_params__.frozen, True)
    return c.bad


def check_died_at_the_batch(mod: types.ModuleType, root: Path) -> list[str]:
    """A process that died after its batch was appended and signed, before the slots' COMMITTED steps: the forecasts are in the log, so they
    keep their life: the missing steps are written at the level that can be shown (C1: the time-stamps' answers
    were not kept with the records), the cycle ends committed, and the slots settle. Slots whose forecasts never reached the log fail as lost."""
    c = Case(mod)
    w = die_at_batch(root)
    before_cycle, before_slots = histories(w)
    (cid,) = before_cycle
    c.eq(
        "the ledger was left mid-cycle",
        (states(before_cycle[cid])[-1], sorted({states(h)[-1] for h in before_slots.values()})),
        ("COMMITTING", ["DECIDED"]),
    )
    c.eq("with the forecasts in the log", len([r for r in w.records() if r["type"] == "forecast"]), 6)
    records, closed = mod.close_lost(w.records(), w.protocol, URN, AT)
    c.eq(
        "one note: the cycle, where it stood, and six slots rolled forward, none failed",
        [(x.cycle_id, x.state_was.value, x.slots_failed, x.slots_left, x.slots_rolled, x.unknown_protocol) for x in closed],
        [(cid, "COMMITTING", 0, 0, 6, False)],
    )
    c.eq("two steps for each slot, and the cycle's own", len(records), 6 * 2 + 1)
    apply(w, records)
    after_cycle, after_slots = histories(w)
    last = after_cycle[cid][-1].step
    c.eq("the cycle is committed, at the time given", (last.state.value, last.reason, last.at_ms), ("COMMITTED", None, AT))
    forecasts = {r["id"]: r for r in w.records() if r["type"] == "forecast"}
    for fid, history in sorted(after_slots.items()):
        committed = next(t.step for t in history if t.step.state is lifecycle.State.COMMITTED)
        stamped = parse_ms(str(forecasts[fid]["committed_at"]))
        c.eq(
            f"{fid[-6:]}: committed at the time the log gave it, at the level the records can show",
            (committed.level, committed.committed_at_ms, states(history)[-1]),
            (forecast.Level.C1, stamped, "AWAITING_ORIGIN"),
        )
    eligible = {next(t.step.eligible_after_ms for t in h if t.step.state is lifecycle.State.AWAITING_ORIGIN) for h in after_slots.values()}
    c.eq(
        "all wait for the origin from the last commit time of the batch (no token counts)",
        eligible,
        {max(parse_ms(str(f["committed_at"])) for f in forecasts.values())},
    )
    count = lifecycle.denominator(after_slots.values())
    c.eq("the denominator counts forecasts and no failures", (count.forecasts, count.failures), (6, 0))
    again, noted = mod.close_lost(w.records(), w.protocol, URN, AT + 1)
    c.eq("nothing is left to close the second time", (again, noted), ((), ()))
    # the same cycle, as it would be if its forecasts had never reached the log: nothing to roll forward, so the slots are lost
    lost, noted_lost = mod.close_lost([r for r in w.records() if r["type"] not in ("forecast", "forecast_state")], w.protocol, URN, AT)
    c.eq("with no forecast in the log a slot has no life to keep", [(x.slots_failed, x.slots_rolled) for x in noted_lost], [])
    w.close()
    gone = die_at_batch(root / "gone")
    kept = [r for r in gone.records() if r["type"] != "forecast"]
    lost, noted_lost = mod.close_lost(kept, gone.protocol, URN, AT)
    c.eq(
        "decided slots whose forecasts are not in the log fail as lost, and the cycle is abandoned",
        ([(x.slots_failed, x.slots_rolled) for x in noted_lost], len(lost)),
        ([(6, 0)], 7),
    )
    gone.close()
    return c.bad


def check_rolled_forward_in_detail(mod: types.ModuleType, root: Path) -> list[str]:
    """What a roll-forward writes, to the letter: each slot committed at its own record's time, all waiting from the batch's last
    commit time, the cycle's commitment named as recovered, and a horizon whose snapshot is too far from the origin voided for the gap as the
    cycle itself would have voided it."""
    c = Case(mod)
    # distinct commit times: the ledger's clock ticks, so every record of the batch has its own
    w = World(root / "tick", ledger_clock=TickClock(NOW, 1.0), attestor=dying_attestor)
    with contextlib.suppress(Died):
        w.run(league)
    forecasts = {r["id"]: parse_ms(str(r["committed_at"])) for r in w.records() if r["type"] == "forecast"}
    c.eq("the six commit times are all different", len(set(forecasts.values())), 6)
    records, closed = mod.close_lost(w.records(), w.protocol, URN, 0)
    apply(w, records)
    cycles, slots = histories(w)
    last = max(forecasts.values())
    for fid, history in sorted(slots.items()):
        committed = next(t.step for t in history if t.step.state is lifecycle.State.COMMITTED)
        waiting = next(t.step for t in history if t.step.state is lifecycle.State.AWAITING_ORIGIN)
        c.eq(
            f"{fid[-6:]}: committed at its own time, waiting from the last of the batch",
            (committed.committed_at_ms, waiting.eligible_after_ms),
            (forecasts[fid], last),
        )
        c.eq(
            f"{fid[-6:]}: no attested time is claimed",
            (waiting.attested_at_ms, waiting.uncertainty_ms, waiting.spread_ms),
            (None, None, None),
        )
        c.eq(
            f"{fid[-6:]}: and the steps follow the commitment in time",
            all(a.step.at_ms <= b.step.at_ms for a, b in pairwise(history)),
            True,
        )
    (cid,) = cycles
    final = cycles[cid][-1].step
    c.eq(
        "the cycle's commitment is named as recovered, from the cycle",
        (final.state.value, final.commitment_id, final.committed_at_ms),
        ("COMMITTED", natural_id("cm", {"cycle": cid, "recovered": "rolled forward"}), last),
    )
    c.eq(
        "the note says six were rolled forward and the cycle was in COMMITTING",
        [(x.slots_rolled, x.state_was.value, x.slots_failed, x.slots_left) for x in closed],
        [(6, "COMMITTING", 0, 0)],
    )
    c.eq(
        "Closed has its rolled count, which is zero when nothing was", mod.Closed("c", cycle.State.PLANNED, 0, 0, False, 0).slots_rolled, 0
    )
    c.eq("and the batch's facts are frozen", mod._Batch.__dataclass_params__.frozen, True)
    w.close()
    # the ledger commits seven minutes after the snapshot: the one-hour horizons are voided for the gap, the others are not
    g = World(root / "gap", ledger_clock=ManualClock(NOW + timedelta(minutes=7)), attestor=dying_attestor)
    with contextlib.suppress(Died):
        g.run(league)
    records, closed = mod.close_lost(g.records(), g.protocol, URN, AT)
    apply(g, records)
    _, slots = histories(g)
    ends = sorted((h[0].step.horizon_ms // HOUR, states(h)[-1]) for h in slots.values())
    c.eq(
        "the one-hour slots are void for the gap and the others wait",
        ends,
        [(1, "VOID")] * 2 + [(4, "AWAITING_ORIGIN")] * 2 + [(24, "AWAITING_ORIGIN")] * 2,
    )
    voided = list(g.records("settlement"))
    c.eq(
        "with a settlement record each, for the gap",
        sorted((r["body"]["state"], r["body"]["reason"]) for r in voided),
        [("VOID", "VOID_GAP")] * 2,
    )
    forecast_records = {r["id"]: r for r in g.records("forecast")}
    for r in voided:
        fid = r["body"]["forecast_id"]
        plain = {k: v for k, v in forecast_records[fid].items() if k != "committed_at"}
        c.eq("each names the forecast record by its digest", r["refs"], [{"id": fid, "sha256": record_digest(plain)}])
        c.eq("and the oracle pair of the protocol", r["body"]["pair_id"], g.protocol.oracle_pair)
    c.eq(
        "the steps of a void slot are the commitment, the wait and the void, in that order",
        next(states(h) for h in slots.values() if h[0].step.horizon_ms == HOUR)[-3:],
        ["COMMITTED", "AWAITING_ORIGIN", "VOID"],
    )
    g.close()
    # a log whose commit times are before the decisions (the ledger's clock was behind): such a forecast cannot be shown committed before it
    # was decided, so recovery does not claim it and does not stop: the slots fail as lost and the cycle is abandoned
    behind = World(root / "behind", ledger_clock=ManualClock(NOW - timedelta(minutes=5)), attestor=dying_attestor)
    with contextlib.suppress(Died):
        behind.run(league)
    records, noted = mod.close_lost(behind.records(), behind.protocol, URN, AT)
    c.eq("commit times before the decisions are not rolled forward", [(x.slots_failed, x.slots_rolled) for x in noted], [(6, 0)])
    apply(behind, records)
    cyc, sl = histories(behind)
    c.eq(
        "the slots are lost and the cycle abandoned, without an error",
        ({states(h)[-1] for h in cyc.values()}, {states(h)[-1] for h in sl.values()}),
        ({"ABANDONED"}, {"FAILED_LOST"}),
    )
    behind.close()
    # a log time after the slot's evidence was frozen and before it was decided is not enough: it must not be before the decision
    skew = World(root / "skew", clock=TickClock(NOW, 1.0), ledger_clock=ManualClock(NOW + timedelta(seconds=19)), attestor=dying_attestor)
    with contextlib.suppress(Died):
        skew.run(league)
    _, before = histories(skew)
    stamped = {r["id"]: parse_ms(str(r["committed_at"])) for r in skew.records() if r["type"] == "forecast"}
    c.eq(
        "the log's time is after the evidence of every slot was frozen and before its decision",
        all(h[-2].step.at_ms <= stamped[fid] < h[-1].step.at_ms for fid, h in before.items()),
        True,
    )
    records, noted = mod.close_lost(skew.records(), skew.protocol, URN, AT)
    c.eq("none is rolled forward", [(x.slots_rolled, x.slots_failed) for x in noted], [(0, 6)])
    skew.close()
    # a slot that is already committed, with its forecast in the log, is left as it is and not committed again
    keep = World(root / "kept", attestor=dying_attestor)
    with contextlib.suppress(Died):
        keep.run(league)
    _, slots_before = histories(keep)
    first = sorted(slots_before)[:2]
    for fid in first:
        h = slots_before[fid]
        h = lifecycle.advance(h, lifecycle.Step(lifecycle.State.COMMITTED, AT - 1000, level=forecast.Level.C1, committed_at_ms=AT - 2000))
        h = lifecycle.advance(h, lifecycle.Step(lifecycle.State.AWAITING_ORIGIN, AT - 1000, eligible_after_ms=AT - 1500))
        apply(keep, [lifecycle.to_record(t, URN, t.step.at_ms) for t in h[-2:]])
    records, noted = mod.close_lost(keep.records(), keep.protocol, URN, AT)
    c.eq(
        "two committed slots are left, the other four are rolled forward",
        [(x.slots_left, x.slots_rolled, x.slots_failed) for x in noted],
        [(2, 4, 0)],
    )
    keep.close()
    return c.bad


def stamped_judge(offsets: list[tuple[str, str, float]], seen: list[list[str]]) -> Any:
    """A judge that answers as the stored time-stamps would: an attestation of the given tokens against the batch's last commit time."""

    def judge(records: list[dict[str, Any]]) -> Any:
        seen.append(sorted(str(r["id"]) for r in records))
        committed_at_ms = max(parse_ms(str(r["committed_at"])) for r in records)
        return rejudge.Judged(attestor(offsets)(bytes(range(32)), committed_at_ms), RULES, "cmjudged")

    return judge


def check_rolled_forward_with_stamps(mod: types.ModuleType, root: Path) -> list[str]:
    """Time-stamps that were kept give the slots the level they support: each slot is judged at its own commit time, the wait for the
    origin follows the attestation, the attested numbers are written for the C3 slots and the cycle names the real commitment."""
    c = Case(mod)
    # twenty seconds between the six commit times: the first records are further from the tokens than the window allows
    w = World(root / "stamped", ledger_clock=TickClock(NOW, 20.0), attestor=dying_attestor)
    with contextlib.suppress(Died):
        w.run(league)
    forecasts = {r["id"]: parse_ms(str(r["committed_at"])) for r in w.records() if r["type"] == "forecast"}
    last = max(forecasts.values())
    seen: list[list[str]] = []
    records, closed = mod.close_lost(w.records(), w.protocol, URN, AT, stamped_judge(GOOD, seen))
    c.eq("the judge is asked once, with the cycle's six forecasts", seen, [sorted(forecasts)])
    c.eq("six were rolled forward", [(x.slots_rolled, x.slots_failed) for x in closed], [(6, 0)])
    apply(w, records)
    cycles, slots = histories(w)
    attestation = attestor(GOOD)(bytes(range(32)), last)
    taken = next(x.step.snapshot_at_ms for h in cycles.values() for x in h if x.step.state is cycle.State.SNAPSHOT_FROZEN)
    levels = {}
    for fid, history in sorted(slots.items()):
        committed = next(t.step for t in history if t.step.state is lifecycle.State.COMMITTED)
        waiting = next(t.step for t in history if t.step.state is lifecycle.State.AWAITING_ORIGIN)
        want = attestation.timing_at(forecasts[fid], RULES).level
        levels[fid] = committed.level.value if committed.level else None
        c.eq(f"{fid[-6:]}: judged at its own commit time", (committed.level.value, committed.committed_at_ms), (want, forecasts[fid]))
        c.eq(f"{fid[-6:]}: waits for the attestation's eligible time", waiting.eligible_after_ms, attestation.eligible_after_ms)
        numbers = (waiting.attested_at_ms, waiting.uncertainty_ms, waiting.spread_ms)
        stated = (attestation.attested_at_ms, attestation.uncertainty_ms, round((attestation.timing.spread_s or 0) * 1000))
        c.eq(f"{fid[-6:]}: the numbers only a C3 slot names", numbers, stated if want == "C3" else (None, None, None))
        gap = void_gap(taken, origin(attestation.eligible_after_ms), history[0].step.horizon_ms)
        c.eq(
            f"{fid[-6:]}: waits for its origin, or is void when the snapshot is too far from it",
            states(history)[-1],
            "VOID" if gap else "AWAITING_ORIGIN",
        )
    c.eq(
        "some slots reach C3 and some do not, so each is judged on its own",
        {"C3" in levels.values(), len(set(levels.values())) > 1},
        {True},
    )
    (cid,) = cycles
    final = cycles[cid][-1].step
    c.eq(
        "the cycle names the commitment the judge gave, at the last commit time",
        (final.state.value, final.commitment_id, final.committed_at_ms),
        ("COMMITTED", "cmjudged", last),
    )
    w.close()
    # one token counts for nothing: the same cycle is judged at C1 and waits from the last commit time, as without time-stamps
    one = World(root / "one", ledger_clock=TickClock(NOW, 1.0), attestor=dying_attestor)
    with contextlib.suppress(Died):
        one.run(league)
    lone = max(parse_ms(str(r["committed_at"])) for r in one.records() if r["type"] == "forecast")
    records, _ = mod.close_lost(one.records(), one.protocol, URN, AT, stamped_judge([("a", "ta", 1)], []))
    apply(one, records)
    _, slots = histories(one)
    got = {
        (n.step.level.value, w_.step.eligible_after_ms, w_.step.attested_at_ms)
        for h in slots.values()
        for n in h
        if n.step.state is lifecycle.State.COMMITTED
        for w_ in h
        if w_.step.state is lifecycle.State.AWAITING_ORIGIN
    }
    c.eq("with one token every slot is C1 and waits from the last commit", got, {("C1", lone, None)})
    one.close()
    # no judge, or a judge that has nothing: what recovery always did
    for label, judge in (("no judge", None), ("a judge with no answer", lambda records: None)):
        n = World(root / label.replace(" ", "_"), attestor=dying_attestor)
        with contextlib.suppress(Died):
            n.run(league)
        records, _ = mod.close_lost(n.records(), n.protocol, URN, AT, judge)
        apply(n, records)
        _, sl = histories(n)
        c.eq(
            f"{label}: C1 and no attested numbers",
            {next(t.step.level.value for t in h if t.step.state is lifecycle.State.COMMITTED) for h in sl.values()},
            {"C1"},
        )
        n.close()
    # the judge is asked only for a cycle with forecasts in the log
    gone = die_at_batch(root / "gone_judge")
    asked: list[list[str]] = []
    mod.close_lost([r for r in gone.records() if r["type"] != "forecast"], gone.protocol, URN, AT, stamped_judge(GOOD, asked))
    c.eq("with no forecast in the log the judge is not asked", asked, [])
    gone.close()
    return c.bad


def check_died_in_collection(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = die_in_collection(root)
    cycles, slots = histories(w)
    (cid,) = cycles
    c.eq("a cycle that died while collecting has no slot records yet", (states(cycles[cid])[-1], slots), ("COLLECTING", {}))
    records, closed = mod.close_lost(w.records(), w.protocol, URN, AT)
    c.eq(
        "but its manifest says which there were: six entered and failed",
        [(x.state_was.value, x.slots_failed) for x in closed],
        [("COLLECTING", 6)],
    )
    c.eq("three records for each, and the cycle's", len(records), 6 * 3 + 1)
    apply(w, records)
    _, after = histories(w)
    c.eq(
        "each is scheduled, created and failed as lost",
        sorted({tuple(states(h)) for h in after.values()}),
        [("SCHEDULED", "CREATED", "FAILED_LOST")],
    )
    c.eq(
        "with the horizon of its slot",
        sorted(h[0].step.horizon_ms for h in after.values()),
        [h * HOUR for h in (1, 1, 4, 4, 24, 24)],
    )
    w.close()
    return c.bad


def check_committed_slots_stay(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = die_at_batch(root)
    _cycles, slots = histories(w)
    keep = sorted(slots)[:2]
    for fid in keep:
        h = slots[fid]
        h = lifecycle.advance(h, lifecycle.Step(lifecycle.State.COMMITTED, AT - 1000, level=forecast.Level.C3, committed_at_ms=AT - 2000))
        h = lifecycle.advance(
            h,
            lifecycle.Step(
                lifecycle.State.AWAITING_ORIGIN,
                AT - 1000,
                eligible_after_ms=AT - 1500,
                attested_at_ms=AT - 1900,
                uncertainty_ms=0,
                spread_ms=0,
            ),
        )
        apply(w, [lifecycle.to_record(t, URN, t.step.at_ms) for t in h[-2:]])
    dropped = [r for r in w.records() if r["type"] != "forecast"]  # the forecasts did not reach the log: nothing to roll forward
    records, closed = mod.close_lost(dropped, w.protocol, URN, AT)
    c.eq("four slots failed, two committed ones left", [(x.slots_failed, x.slots_left) for x in closed], [(4, 2)])
    apply(w, records)
    _, after = histories(w)
    c.eq("the committed ones go on", sorted(states(after[f])[-1] for f in keep), ["AWAITING_ORIGIN"] * 2)
    c.eq("the others are failed", sorted(states(after[f])[-1] for f in after if f not in keep), ["FAILED_LOST"] * 4)
    w.close()
    return c.bad


def check_kinds_of_cycle(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    # a cycle that is over is left alone
    done = World(root / "done")
    done.run(league)
    c.eq("a committed cycle needs nothing", mod.close_lost(done.records(), done.protocol, URN, AT), ((), ()))
    done.close()
    # one that only got as far as being planned
    planned = World(root / "planned")
    history = cycle.begin(planned.protocol.sha256, SLOT, int(NOW.timestamp() * 1000))
    apply(planned, [cycle.to_record(history[0], URN, history[0].step.at_ms)])
    records, closed = mod.close_lost(planned.records(), planned.protocol, URN, AT)
    c.eq(
        "a cycle that was only planned is abandoned, with no slots",
        [(x.state_was.value, x.slots_failed, x.slots_left) for x in closed],
        [("PLANNED", 0, 0)],
    )
    c.eq("by one record", len(records), 1)
    planned.close()
    # one that ran under another protocol
    other = die_at_batch(root / "other")
    changed: Protocol = make(
        protocol_module, name="another", primary_venue="binance-spot", second_venue="okx", schedule_start_ms=SLOT, arms=other.protocol.arms
    )
    records, closed = mod.close_lost(other.records(), changed, URN, AT)
    c.eq(
        "a cycle of another protocol is closed and its slots are not looked for",
        ([(x.state_was.value, x.slots_failed, x.unknown_protocol) for x in closed], len(records)),
        ([("COMMITTING", 0, True)], 1),
    )
    other.close()
    # the time of the closing is never before the last step
    late = World(root / "late", clock=TickClock(NOW, 0.01), attestor=dying_attestor)
    with contextlib.suppress(Died):
        late.run(league)
    records, closed = mod.close_lost(late.records(), late.protocol, URN, 0)
    apply(late, records)
    cycles, _ = histories(late)
    (history,) = cycles.values()
    c.eq("closed no earlier than the last step it follows", history[-1].step.at_ms >= history[-2].step.at_ms, True)
    _, slots = histories(late)
    c.eq("and so are the slots", all(h[-1].step.at_ms >= h[-2].step.at_ms for h in slots.values()), True)
    # a batch that was only partly appended: the slots are in the ledger, decided later than the cycle's last step
    part = World(root / "part", clock=TickClock(NOW, 0.01), attestor=dying_attestor)
    with contextlib.suppress(Died):
        part.run(league)
    cut = [r for r in part.records() if not (r["type"] == "cycle_state" and r["body"]["state"] == "COMMITTING")]
    more, noted = mod.close_lost(cut, part.protocol, URN, 0)
    after = lifecycle.rebuild([*(r for r in cut if r["type"] == "forecast_state"), *(r for r in more if r["type"] == "forecast_state")])
    c.eq("the slots are failed no earlier than they were decided", all(h[-1].step.at_ms >= h[-2].step.at_ms for h in after.values()), True)
    c.eq("all six were", [(x.state_was.value, x.slots_failed) for x in noted], [("RUNNING", 6)])
    late.close()
    part.close()
    return c.bad


def check_over_already(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)

    def script(call: Call, world: World) -> Reply:
        if call.operation == "candles" and call.args["instrument"] == "ETHUSDT":
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM")
        return world.default(call)

    w = World(root, script=script, attestor=dying_attestor)
    with contextlib.suppress(Died):
        w.run(league)
    records, closed = mod.close_lost(w.records(), w.protocol, URN, AT)
    c.eq(
        "a slot that had already ended is left as it was, and the decided ones, whose forecasts are in the log, are rolled forward",
        [(x.slots_failed, x.slots_left, x.slots_rolled) for x in closed],
        [(0, 3, 3)],
    )
    apply(w, records)
    _, after = histories(w)
    c.eq(
        "the failures of evidence stay, the decided ones go on to their origin",
        sorted(states(h)[-1] for h in after.values()),
        ["AWAITING_ORIGIN"] * 3 + ["FAILED_EVIDENCE"] * 3,
    )
    w.close()
    return c.bad


def check_order(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    for k in range(12):
        history = cycle.begin(w.protocol.sha256, SLOT + k * HOUR, SLOT + k * HOUR)
        apply(w, [cycle.to_record(history[0], URN, history[0].step.at_ms)])
    records, closed = mod.close_lost(w.records(), w.protocol, URN, SLOT + 20 * HOUR)
    c.eq("twelve cycles are closed in the order of their ids", [x.cycle_id for x in closed], sorted(x.cycle_id for x in closed))
    c.eq("a record for each, in that order", [r["body"]["cycle_id"] for r in records], [x.cycle_id for x in closed])
    w.close()
    return c.bad


def check_died_at_the_batch_with_barrier_slots(mod: types.ModuleType, root: Path) -> list[str]:
    """A cycle whose rules arm also forecasts contract B has twelve slots, not six, and the recovery of a process lost at the batch looks
    for all of them: the contract B forecasts in the log are rolled forward like the others, and with no forecast in the log every one of
    the twelve slots fails as lost (so that none leaves the denominator)."""
    from tests.league_checks import barrier_map, barrier_world

    c = Case(mod)
    bm = barrier_map()
    w = barrier_world(root / "b", bm, attestor=dying_attestor)
    with contextlib.suppress(Died):
        w.run(league, barrier_mappings={"L0": bm})
    contracts = sorted({h[0].step.contract for h in histories(w)[1].values()})
    c.eq("the cycle made slots of both contracts", contracts, ["B", "D"])
    c.eq("with all twelve forecasts in the log", len([r for r in w.records() if r["type"] == "forecast"]), 12)
    _, closed = mod.close_lost(w.records(), w.protocol, URN, AT)
    c.eq("all twelve are rolled forward, none failed", [(x.slots_failed, x.slots_rolled) for x in closed], [(0, 12)])
    w.close()
    gone = barrier_world(root / "gone", bm, attestor=dying_attestor)
    with contextlib.suppress(Died):
        gone.run(league, barrier_mappings={"L0": bm})
    kept = [r for r in gone.records() if r["type"] not in ("forecast", "forecast_state")]
    _, noted = mod.close_lost(kept, gone.protocol, URN, AT)
    c.eq("with no forecast in the log all twelve slots fail as lost", [(x.slots_failed, x.slots_rolled) for x in noted], [(12, 0)])
    gone.close()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_died_at_the_batch,
        check_died_at_the_batch_with_barrier_slots,
        check_died_in_collection,
        check_rolled_forward_in_detail,
        check_rolled_forward_with_stamps,
        check_committed_slots_stay,
        check_kinds_of_cycle,
        check_over_already,
        check_order,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1100:]}")
    return bad
