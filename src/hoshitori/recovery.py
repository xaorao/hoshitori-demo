"""Closing the cycles a lost process left open: from the records alone, what to append so that every cycle ends and every slot is counted.

``CYCLE_RUNNER_SPEC`` section 4 and contract sections 16 and 17 are the rules. A cycle that is not over when a new process looks at the
ledger can only have been left by a process that is gone, so it is closed as abandoned for a lost process. Its slots are found from its
sealed manifest (the cycle's id, the protocol's arms and horizons and the manifest's included assets give every slot's id): a slot that was
decided and whose forecast is not in the log is ``FAILED_LOST``, a slot with no history yet is entered and failed, and a slot that was
committed is left alone, because a committed forecast is never abandoned. A slot that was decided and whose forecast IS in the log (the
batch was appended and signed and the process died before the slots' steps) keeps its life: its COMMITTED and
AWAITING_ORIGIN steps are written, and the cycle ends committed. The level of each is what the time-stamps kept for the batch support:
``judge`` (``rejudge.Rejudger``) reads the authorities' stored answers back from the capture store, finds the signed checkpoint they
stamped and judges them as the live run did, so a batch that was stamped before the process died keeps C3 and the origin that follows from
its tokens, and the cycle names the real commitment; with no judge, or when no answer was kept (the process died before the authorities were
asked), the level is C1 and the wait for the origin runs from the last commit time of the batch. Nothing here reads a clock, the capture
store or the ledger itself: the records, the time and the judge are given.

* **A cycle is resumed by nobody.** The snapshot of a cycle is held in memory until its batch is committed, so no restart can continue one.
* **Another protocol's cycle is closed but its slots are not found** (the arms and horizons that made them are not known here); that is
  reported, not guessed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, cast

from hoshitori import canonical, cycle, forecast, lifecycle, settlement, settlement_record
from hoshitori.evidence import record_digest
from hoshitori.ids import natural_id
from hoshitori.protocol import Protocol
from hoshitori.rejudge import Judged
from hoshitori.timeline import origin, to_ms, void_gap
from hoshitori.timeutil import parse_rfc3339

HOUR_MS: Final = 3_600_000
LOST: Final = "PROCESS_LOST"
SLOT_REASON: Final = "the process was lost during the cycle"

# a cycle's forecast records in, what the stored time-stamps support for their batch out
Judge = Callable[[list[dict[str, Any]]], Judged | None]


@dataclass(frozen=True)
class Closed:
    cycle_id: str
    state_was: cycle.State
    slots_failed: int  # slots decided and not committed, or entered here, that are now failed
    slots_left: int  # slots that were committed (they go on to settle) or were over already
    unknown_protocol: bool  # the cycle ran under another protocol: its slots were not looked for
    slots_rolled: int  # slots whose forecast was in the log and whose steps were written here: they go on to settle


def _of(records: Iterable[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r for r in records if r.get("type") == kind]


def close_lost(
    records: Sequence[dict[str, Any]], protocol: Protocol, source_urn: str, now_ms: int, judge: Judge | None = None
) -> tuple[tuple[dict[str, canonical.JsonValue], ...], tuple[Closed, ...]]:
    """The records that end every cycle left open, and a note of what was done for each. ``judge``, when given, says what the time-stamps
    kept for a cycle's batch support; without it, or when it has no answer, a rolled-forward slot is at C1."""
    cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], _of(records, cycle.TYPE)))
    slots = lifecycle.rebuild(cast(list[dict[str, canonical.JsonValue]], _of(records, lifecycle.TYPE)))
    manifests = {r["id"]: r for r in _of(records, "manifest")}
    forecasts = {r["id"]: r for r in _of(records, "forecast")}
    out: list[dict[str, canonical.JsonValue]] = []
    closed: list[Closed] = []
    for cycle_id, history in sorted(cycles.items()):
        state = cycle.current_state(history)
        if state in cycle.TERMINAL:
            continue
        at = max(now_ms, history[-1].step.at_ms)
        same = history[0].step.protocol_hash == protocol.sha256
        failed = left = rolled = 0
        manifest = manifests.get(natural_id("mf", {"cycle": cycle_id}))
        # a batch is in flight only in COMMITTING: only then can the forecasts in the log be the ones this cycle committed
        batch = _batch(cycle_id, history, forecasts, judge) if state is cycle.State.COMMITTING else None
        if same and manifest is not None:
            for fid, contract, horizon in _slot_ids(cycle_id, protocol, manifest):
                got = slots.get(fid)
                if batch is not None and got is not None and _rolls_forward(got, forecasts.get(fid)):
                    _roll_forward(got, forecasts[fid], batch, protocol, at, source_urn, out)
                    rolled += 1
                    continue
                moved = _close_slot(got, fid, contract, horizon, at, source_urn, out)
                failed, left = failed + moved, left + (1 - moved)
        if rolled:
            final = cycle.Step(
                cycle.State.COMMITTED,
                max(at, cast(_Batch, batch).committed_at_ms),
                commitment_id=_commitment_id(cycle_id, cast(_Batch, batch)),
                committed_at_ms=cast(_Batch, batch).committed_at_ms,
            )
            ended = cycle.advance(history, final)
        else:
            ended = cycle.advance(history, cycle.abandon_step(LOST, at))
        out.append(cycle.to_record(ended[-1], source_urn, at))
        closed.append(Closed(cycle_id, state, failed, left, not same, rolled))
    return tuple(out), tuple(closed)


@dataclass(frozen=True)
class _Batch:
    committed_at_ms: int  # the last commit time of the cycle's batch: with no token counting, nothing derived may start before it
    taken_ms: int | None  # when the snapshot was taken, for the gap a horizon allows
    judged: Judged | None = None  # what the kept time-stamps support, when they were found


def _commitment_id(cycle_id: str, batch: _Batch) -> str:
    """The commitment the cycle names: the real one when the stamped checkpoint was found, otherwise one that says it was recovered."""
    if batch.judged is not None:
        return batch.judged.commitment_id
    return natural_id("cm", {"cycle": cycle_id, "recovered": "rolled forward"})


def _batch(cycle_id: str, history: cycle.History, forecasts: dict[str, dict[str, Any]], judge: Judge | None) -> _Batch | None:
    """What the cycle's batch left in the log: the commit time of its last forecast (none if no forecast of this cycle is there) and what
    the kept time-stamps support for it."""
    mine = [r for r in forecasts.values() if r["body"].get("cycle_id") == cycle_id]
    taken = next((t.step.snapshot_at_ms for t in history if t.step.state is cycle.State.SNAPSHOT_FROZEN), None)
    if not mine:
        return None
    last = max(to_ms(parse_rfc3339(str(r["committed_at"]))) for r in mine)
    judged = None if judge is None else judge(mine)
    return _Batch(last if judged is None else judged.attestation.committed_at_ms, taken, judged)


def _rolls_forward(history: lifecycle.History, record: dict[str, Any] | None) -> bool:
    """Whether a decided slot has a forecast in the log that it can be shown to have committed: the log's commit time is not before the
    decision (a log whose clock was behind cannot say the forecast was committed before it was decided, and recovery does not claim it)."""
    return (
        record is not None
        and lifecycle.current_state(history) is lifecycle.State.DECIDED
        and to_ms(parse_rfc3339(str(record["committed_at"]))) >= history[-1].step.at_ms
    )


def _roll_forward(
    history: lifecycle.History,
    record: dict[str, Any],
    batch: _Batch,
    protocol: Protocol,
    at: int,
    source_urn: str,
    out: list[dict[str, canonical.JsonValue]],
) -> None:
    """The steps a slot missed whose forecast is in the log: committed at the time the log gave it, waiting for its origin, and void for a
    gap as the cycle would have made it (a snapshot too long before the origin)."""
    committed = to_ms(parse_rfc3339(str(record["committed_at"])))
    moment = max(at, committed)  # ``at`` is not before the cycle's last step, which follows every step of its slots
    known = len(history)
    judged = batch.judged
    level = forecast.Level.C1 if judged is None else forecast.Level(judged.level_at(committed))
    history = lifecycle.advance(history, lifecycle.Step(lifecycle.State.COMMITTED, moment, level=level, committed_at_ms=committed))
    wait = lifecycle.Step(
        lifecycle.State.AWAITING_ORIGIN,
        moment,
        eligible_after_ms=batch.committed_at_ms if judged is None else judged.attestation.eligible_after_ms,
    )
    if judged is not None and level is forecast.Level.C3:  # a C3 slot names the numbers its origin follows from, as the cycle writes it
        attestation = judged.attestation
        wait = replace(
            wait,
            attested_at_ms=attestation.attested_at_ms,
            uncertainty_ms=attestation.uncertainty_ms,
            spread_ms=round(cast(float, attestation.timing.spread_s) * 1000),
        )
    history = lifecycle.advance(history, wait)
    out.extend(lifecycle.to_record(t, source_urn, t.step.at_ms) for t in history[known:])
    horizon = history[0].step.horizon_ms
    eligible = batch.committed_at_ms if judged is None else judged.attestation.eligible_after_ms
    if batch.taken_ms is not None and horizon is not None and void_gap(batch.taken_ms, origin(eligible), horizon):
        voided = settlement.void(str(record["id"]), cast(str, history[0].step.contract), protocol.oracle_pair, "VOID_GAP")
        plain = {k: v for k, v in record.items() if k != "committed_at"}
        out.append(settlement_record.to_record(voided, record_digest(plain), source_urn, moment))
        step = lifecycle.Step(lifecycle.State.VOID, moment, reason="VOID_GAP", settlement_id=voided.settlement_id)
        end = lifecycle.advance(history, step)
        out.extend(lifecycle.to_record(t, source_urn, t.step.at_ms) for t in end[len(history) :])


def _slot_ids(cycle_id: str, protocol: Protocol, manifest: dict[str, Any]) -> list[tuple[str, str, int]]:
    """``(forecast id, contract, horizon)`` of every slot the cycle created: the protocol's plan for each included candidate."""
    assets = [e["asset_id"] for e in manifest["body"]["entries"] if e["inclusion"] == "INCLUDED"]
    return [
        (forecast.forecast_id(cycle_id, arm, asset, contract, hours * HOUR_MS), contract, hours * HOUR_MS)
        for asset in assets
        for arm, contract, hours in protocol.slot_plan()
    ]


def _close_slot(
    history: lifecycle.History | None,
    fid: str,
    contract: str,
    horizon: int,
    at: int,
    source_urn: str,
    out: list[dict[str, canonical.JsonValue]],
) -> int:
    """Fail the slot if it was not committed; returns 1 if it was failed here and 0 if it was left (committed, or already over)."""
    fresh = history is None
    if history is None:
        history = lifecycle.begin(fid, contract, horizon, at)
        history = lifecycle.advance(history, lifecycle.Step(lifecycle.State.CREATED, at))
    state = lifecycle.current_state(history)
    if state in lifecycle.TERMINAL:
        return 0
    if state not in (lifecycle.State.SCHEDULED, lifecycle.State.CREATED, lifecycle.State.EVIDENCE_FROZEN, lifecycle.State.DECIDED):
        return 0  # committed: it goes on to its origin and its settlement
    known = 0 if fresh else len(history)
    step = lifecycle.Step(lifecycle.State.FAILED_LOST, max(at, history[-1].step.at_ms), reason=SLOT_REASON)
    ended = lifecycle.advance(history, step)
    out.extend(lifecycle.to_record(t, source_urn, t.step.at_ms) for t in ended[known:])
    return 1
