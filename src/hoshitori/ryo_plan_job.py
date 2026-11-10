"""Settling arm R's plans: one pass over the reads whose plan is not settled yet, as often as the scheduler likes (``ryo_plan``).

A read's plan is settled on the window its verdict is scored on: the 24-hour directional call of the same cycle on the same token
(``arm_r``). The pass waits until that call is settled, because its settlement holds the window's start price ``P0`` (agreed by both
oracles, written once) and its record names the oracle pair. Then:

* the window was not settled (void or unsettleable): the plan takes the window's state and reason, and no bar is read;
* the start price is at or beyond one of the plan's levels: the plan is ``ALREADY_PAST`` and no bar is read;
* no oracle pair is known for the token at the end of the window (the window's call was settled on one, so the registry contradicts
  itself): the plan is reported as failed and left for a later pass, never settled on a guess;
* otherwise the window's one-minute bars are read through the gateway, as the settlement pass reads a barrier call's, and the plan is
  settled by ``settlement.settle_barrier``. A bar that is missing keeps the plan pending for the retry window of an hour after the window
  ended; then it is unsettleable, as a barrier call would be.

A pass writes only new records, one per read at most; a read whose plan is already settled, or that has no plan, is passed over.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from hoshitori import canonical, forecast, lifecycle, oracle, ryo_plan, ryo_read, settlement, settlement_record
from hoshitori.settle_job import DEFERRED, RETRY_WINDOW_MS, SettleServices, oracle_pair, read_bars
from hoshitori.settlement import State
from hoshitori.timeline import MINUTE_MS, from_ms, reference_candle_open

PURPOSE: Final = "ryo-plan"  # the caller the gateway records for the bars this pass reads
NO_PAIR: Final = "no oracle pair is known for the token at the end of its window, though the window's own call was settled"


@dataclass(frozen=True)
class Passed:
    records: tuple[dict[str, canonical.JsonValue], ...]  # new plan settlements, in read order
    pending: tuple[str, ...]  # reads whose window is settled and whose plan cannot be decided yet
    failed: tuple[tuple[str, str], ...] = ()  # (read, reason): a plan that could not be settled, left for the next pass


@dataclass(frozen=True)
class _Plan:
    read_id: str
    plan: ryo_read.Plan
    window: ryo_plan.Window
    made: forecast.Forecast  # the window's 24-hour call
    ended: settlement.Settlement  # its settlement


def _clean(record: Mapping[str, Any]) -> dict[str, canonical.JsonValue]:
    return {k: v for k, v in record.items() if k != "committed_at"}


def _ready(records: Sequence[Mapping[str, Any]]) -> list[_Plan]:
    """The reads with a plan and no plan settlement whose window's 24-hour call is settled, in ledger order."""
    done = {str(r["body"]["read_id"]) for r in records if r.get("type") == ryo_plan.TYPE}
    manifests = {
        (str(r["body"]["cycle_id"]), str(e["candidate_id"])): str(e["asset_id"])
        for r in records
        if r.get("type") == "manifest"
        for e in r["body"]["entries"]
    }
    calls: dict[tuple[str, str], forecast.Forecast] = {}
    for r in records:
        if r.get("type") == forecast.TYPE:
            made = forecast.from_record(_clean(r))
            common = made.common
            if common.contract == "D" and common.horizon_ms == ryo_plan.HORIZON_MS:
                calls.setdefault((common.cycle_id, common.asset_id), made)  # every arm's call of one window shares it: the first will do
    ended = {
        s.forecast_id: s for s, _ in (settlement_record.from_record(_clean(r)) for r in records if r.get("type") == settlement_record.TYPE)
    }
    histories = lifecycle.rebuild([_clean(r) for r in records if r.get("type") == lifecycle.TYPE])
    out = []
    for r in records:
        if r.get("type") != ryo_read.TYPE or r["id"] in done:
            continue
        body = ryo_read.from_record(_clean(r))
        if body["plan"] is None:
            continue
        cycle_id, candidate_id = str(body["cycle_id"]), str(body["candidate_id"])
        call = calls.get((cycle_id, manifests.get((cycle_id, candidate_id), "")))
        if call is None:
            continue  # no window: the plan, like the verdict, is never scored
        made = call
        settled = ended.get(made.common.forecast_id)
        if settled is None:
            continue  # the window has not been settled yet
        opened = next(t.step.origin_ms for t in histories[made.common.forecast_id] if t.step.state is lifecycle.State.OPEN)
        plan = cast(dict[str, str], body["plan"])
        out.append(
            _Plan(
                str(r["id"]),
                ryo_read.Plan(plan["entry"], plan["stop"], plan["target"]),
                ryo_plan.Window(made.common.forecast_id, cast(int, opened), made.common.quote_class, settled.pair_id, settled.p0),
                made,
                settled,
            )
        )
    return out


def plan_pass(sv: SettleServices, records: Sequence[Mapping[str, Any]], now_ms: int, *, wait_s: float = 30.0) -> Passed:
    """Settle every plan that can be settled now."""
    out: list[dict[str, canonical.JsonValue]] = []
    pending: list[str] = []
    failed: list[tuple[str, str]] = []
    reading: list[tuple[_Plan, settlement.Barrier, oracle.Pick]] = []
    for p in _ready(records):
        try:
            if p.ended.state not in (State.SETTLED, State.VOID) or (p.ended.state is State.VOID and p.ended.reason != "VOID_TIE"):
                out.append(_record(sv, p, _as_window(p), now_ms))
                continue
            barrier = ryo_plan.barrier_of(p.read_id, p.plan, p.window)
            if barrier is None:
                out.append(_record(sv, p, None, now_ms))
                continue
            common = p.made.common
            pick = oracle.pick_sealed(
                sv.env.registry, sv.protocol, common.asset_id, from_ms(common.as_of_ms), from_ms(p.window.origin_ms + ryo_plan.HORIZON_MS)
            )
            if pick is None:  # the window's own call was settled on a pair known by then: none now means the registry contradicts it
                failed.append((p.read_id, NO_PAIR))
                continue
            reading.append((p, barrier, pick))
        except (ryo_plan.RyoPlanError, settlement.SettlementError, ValueError, KeyError) as exc:
            failed.append((p.read_id, str(exc)[:200]))
    if reading:
        wanted: dict[tuple[str, str], list[int]] = {}
        needs: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}
        for p, _, pick in reading:
            reference = reference_candle_open(p.window.origin_ms)
            window = tuple(p.window.origin_ms + k * MINUTE_MS for k in range(ryo_plan.HORIZON_MS // MINUTE_MS))
            needs[p.read_id] = ((reference, *window), (reference,))  # what settle_job reads for a barrier call of the same window
            wanted.setdefault((pick.primary.venue, pick.primary.raw_id), []).extend(needs[p.read_id][0])
            wanted.setdefault((pick.second.venue, pick.second.raw_id), []).extend(needs[p.read_id][1])
        read = read_bars(sv.env, wanted, wait_s, None, PURPOSE)
        for p, barrier, pick in reading:
            exhausted = now_ms - (p.window.origin_ms + ryo_plan.HORIZON_MS) >= RETRY_WINDOW_MS
            try:
                pair = oracle_pair(pick, read, p.window.pair_id, needs[p.read_id], exhausted=exhausted)
                if isinstance(pair, str):
                    if pair == DEFERRED:
                        pending.append(p.read_id)
                    else:
                        out.append(_record(sv, p, settlement.void(p.read_id, "B", p.window.pair_id, pair), now_ms))
                    continue
                ended = settlement.settle_barrier(barrier, pair, retries_exhausted=exhausted)
                if ended.state is State.PENDING:
                    pending.append(p.read_id)
                else:
                    out.append(_record(sv, p, ended, now_ms))
            except (ryo_plan.RyoPlanError, settlement.SettlementError, ValueError, KeyError) as exc:
                failed.append((p.read_id, str(exc)[:200]))
    order = {str(r["id"]): n for n, r in enumerate(records)}
    out.sort(key=lambda rec: order[str(cast(dict[str, Any], rec["body"])["read_id"])])  # every plan settled here is of a read in records
    return Passed(tuple(out), tuple(pending), tuple(failed))


def _as_window(p: _Plan) -> settlement.Settlement:
    """The plan's settlement when its window could not be settled: the window's own state and reason, and no outcome."""
    return settlement.Settlement(
        ryo_plan.record_id(p.read_id),
        p.read_id,
        "B",
        p.window.pair_id,
        settlement.RULE_VERSION,
        p.ended.state,
        None,
        p.ended.reason or p.ended.state.value,
        None,
        None,
        None,
        (),
    )


def _record(sv: SettleServices, p: _Plan, ended: settlement.Settlement | None, now_ms: int) -> dict[str, canonical.JsonValue]:
    return ryo_plan.to_record(ryo_plan.body_of(p.read_id, p.plan, p.window, ended), sv.source_urn, now_ms)
