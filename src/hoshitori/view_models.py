"""The typed view models of the interface, built from ledger records only (``architecture/UX_SPEC.md`` section 1).

A page is a function of these values. Every string on them is one of three things: a number or id read from a record, a word from the closed
lists of ``labels``, or a time. There is no field that can carry free text from a model, and nothing is computed that the records do not
hold: a probability is the committed one, a score is the mean of ``scoring.brier`` over settled forecasts that count towards a headline
(``lifecycle.headline_eligible``: reached C3 by its origin and still holds C3 or more, contract sections 8 and 17), with the mean over every
settled forecast and the share of those that missed C3 beside it, a count is the lifecycle denominator. Evidence is listed by its id,
source, time and digest and never by its values, because the evidence of an exchange is held
for private use (``redistribution``). A number that is unknown is ``None``, never zero, and so is a time that is unknown: a page reads a
time as a time.

* **Rows.** One row for every slot of every cycle (a call, an abstention, a failure, a horizon voided), in cycle order, so the table of a
  cycle adds up to the slots scheduled.
* **Receipts.** One per slot that made a call: the call, what would prove it wrong, the evidence it read, every step of its history, its
  commitment in words and exactly, its settlement and, for a model arm, what the arm did.
* **The record.** One block per arm: the denominator of ``lifecycle.denominator``, coverage and availability from ``scoring.coverage`` and,
  for contract D, the mean Brier score of the settled calls that count towards a headline with their number, the same over every settled
  call, and the share of those that missed C3 (contract section 8: the headline uses the C3-and-above subset, a second line shows all
  committed levels and the share that missed C3). It ranks nothing: a score is "provisional" until a
  protocol's own minimum is met, and no minimum is met by an exploratory run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any, Final, cast

from hoshitori import (
    arm_r,
    arm_run_record,
    canonical,
    changes,
    comparison,
    cycle,
    forecast,
    gap_record,
    labels,
    lifecycle,
    ryo_plan,
    ryo_read,
    scoring,
    settlement_record,
)
from hoshitori.evidence import record_digest
from hoshitori.protocol import Protocol
from hoshitori.settlement import Settlement
from hoshitori.settlement import State as SettlementState
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

MODE_EXPLORATORY: Final = "Exploratory run: not a scored period"
MODE_SCORED: Final = "Scored period"
PLACES: Final = Decimal("0.0001")


class ViewError(ValueError):
    """Records that do not describe a ledger the interface can show."""


@dataclass(frozen=True)
class Row:
    forecast_id: str
    cycle_id: str
    arm: str
    arm_name: str
    symbol: str
    hours: int
    contract: str  # D: the price is higher at the horizon; B: the target, the stop or neither is reached first
    state: str  # one of ROW_STATES
    chance: str | None  # the committed probability in words; none when no call was made
    result: str | None  # what happened, once settled
    reason: str | None  # in words
    reason_code: str | None  # as the records write it
    trust: str  # one of TRUST_PHRASES
    level: str | None  # exactly, for receipts and tables that show it (C1 to C4)
    origin: str | None
    settled_at: str | None  # when its settlement was recorded
    due: str | None  # the earliest the call can settle: its origin (or, until that is known, its commitment) plus its horizon


@dataclass(frozen=True)
class EvidenceLine:
    evidence_id: str
    source: str
    path: str
    kind: str
    event_time: str | None  # none for evidence the ledger does not hold
    data_mode: str
    origin_family: str
    raw_sha256: str | None
    shown: str  # what a visitor may see of it: "digest only" for held-back data


@dataclass(frozen=True)
class UnreadLine:
    """A source asked for the evidence of a call that brought nothing: the snapshot of its cycle names it, and so does the receipt."""

    source: str
    reason_code: str
    reason: str  # in words
    interval_s: int | None  # the window that failed, where one did


@dataclass(frozen=True)
class TimelineLine:
    state: str
    at: str
    reason: str | None


@dataclass(frozen=True)
class SettlementLine:
    state: str
    outcome: str | None
    reference_price: str | None
    outcome_price: str | None
    oracles: str
    rule_version: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class AgentChallenge:
    """What the debate's Bear put against the Bull's case: the items it cited that the Bull did not, which of them the judge cited, and how
    many items both cited. An agent's challenge: never a person's, and counted toward no one's record."""

    bear_only: tuple[EvidenceLine, ...]
    judge_cited: tuple[str, ...]  # the ids among them that the judge cited
    shared: int
    bull_only: int


@dataclass(frozen=True)
class RunLine:
    verdict: str
    reason: str | None
    gate: str
    proposed_chance: str | None
    final_chance: str | None
    calls: int
    tokens: int
    cutoff_s: int
    challenge: AgentChallenge | None = None  # the debate's Bear against the Bull, when the debate ran to the judge


@dataclass(frozen=True)
class Receipt:
    row: Row
    invalidations: tuple[str, ...]
    evidence: tuple[EvidenceLine, ...]
    timeline: tuple[TimelineLine, ...]
    committed_at: str | None
    as_of: str
    oracles: str
    forecast_digest: str
    settlement: SettlementLine | None
    run: RunLine | None
    unread: tuple[UnreadLine, ...] = ()  # what was asked for its evidence and not read, in the order the snapshot gives
    evidence_age_s: int | None = (
        None  # the origin less the evidence time, in seconds (contract section 25, item 8); none until the origin is set
    )
    gap_drift_atr: str | None = None  # |reference price - close| over ATR(14), once the call has settled with a reference price; else none


@dataclass(frozen=True)
class StratumLine:
    """One third of an arm's settled calls, by the age of their evidence at the origin or by the drift since it: how many, their score."""

    by: str  # "age" or "drift"
    group: int  # 0 the lowest third, 1 the middle, 2 the highest
    low: str | None  # the lowest value in the group, in the unit of the split (minutes of age, or ATR); none for an empty group
    high: str | None
    n: int
    mean_brier: str | None


@dataclass(frozen=True)
class ReliabilityLine:
    """One third of an arm's directional calls by the probability they said: how many, the mean of what was said, how often it rose."""

    group: int  # 0 the lowest probabilities, 2 the highest
    n: int
    mean_p: str  # the mean probability said, four places
    frequency: str  # the share of those calls that rose, four places
    low: str  # the lowest probability in the group
    high: str


@dataclass(frozen=True)
class ExclusionLine:
    """Calls that were not scored, counted by the reason: never dropped without being shown (contract section 7)."""

    reason: str  # the code of the settlement's reason
    words: str  # the same in words for a reader
    n: int


@dataclass(frozen=True)
class BoundsLine:
    """What the score and its skill against a coin could be if every call left out of them had gone either way (contract section 7)."""

    excluded: int
    brier_low: str
    brier_high: str
    skill_low: str  # against a coin, the lowest it could be
    skill_high: str
    sensitive: (
        bool  # the skill is above zero at one end or in the headline and below zero at the other: the result depends on what was left out
    )


@dataclass(frozen=True)
class ArmRecord:
    arm: str
    arm_name: str
    contract: str
    scheduled: int
    calls: int
    settled: int
    open: int
    not_settled: int  # ambiguous, unsettleable and void
    abstained: int
    failed: int
    deciding: int
    coverage: str | None
    availability: str | None
    mean_brier: (
        str | None
    )  # the settled calls that count towards a headline (lifecycle.headline_eligible): D, the Brier score; B, the three-class one
    brier_n: int
    all_mean_brier: str | None  # every settled call, whatever its level
    all_brier_n: int
    missed_c3: str | None  # of every settled call, the share that does not count (missed C3); none when no call has settled
    label: str
    skill: str | None = None  # 1 minus the headline score over the score of the fixed reference (a coin for D, three equal parts for B)
    strata: tuple[StratumLine, ...] = ()  # the score of the calls that settled, split in thirds by the age of the evidence and by the drift
    log_score: str | None = None  # D: the mean logarithmic score of the calls that count towards the headline (lower is better)
    climate_skill: str | None = None  # D: 1 minus the Brier score over that of the share of rises in the 30 days before each call
    climate_n: int = 0  # the calls that had such a reference (enough outcomes in the 30 days before them)
    reliability: tuple[ReliabilityLine, ...] = ()  # D: the headline calls in three equal-count groups by the probability said
    exclusions: tuple[ExclusionLine, ...] = ()  # the calls that were void or unsettleable, by reason
    bounds: BoundsLine | None = (
        None  # D: the score and its skill if every excluded call had gone either way; none when nothing was excluded
    )


@dataclass(frozen=True)
class SourceRead:
    """What one source brought into a cycle's snapshot: how many evidence items, and for how many candidates it brought nothing."""

    source: str
    items: int
    unread: int


@dataclass(frozen=True)
class CycleSummary:
    cycle_id: str
    planned_for: str
    state: str
    committed_at: str | None
    slots: int
    rows_by_state: tuple[tuple[str, int], ...]
    sources: tuple[SourceRead, ...] = ()


@dataclass(frozen=True)
class TodayChange:
    """One line of the first card of Today: what changed, in a closed sentence, and the one thing to do about it."""

    kind: str  # FLIP, MISS, STOPPED, HIT, MOVE or NEW (changes.Kind)
    forecast_id: str  # the receipt it opens
    arm_name: str
    symbol: str
    hours: int
    sentence: str
    action: str
    size: str  # what the kind sorted on, as text


@dataclass(frozen=True)
class Today:
    mode: str
    generated_at: str
    latest: CycleSummary | None
    rows: tuple[Row, ...]  # the latest cycle's slots
    settled_lately: tuple[Row, ...]
    since: str | None  # when the previous cycle was committed: what changed is counted from then
    settled_since: tuple[tuple[str, int], ...]  # results of the calls settled since, in a fixed order
    product: str  # one sentence: what this is
    rule: str  # the printed rule that orders ``changes``
    changes: tuple[TodayChange, ...]  # the top changes since the previous cycle, or the first cycle's calls; at most three
    in_progress: str | None = None  # when the cycle now being collected was planned, when one is newer than the latest committed
    scheduled_cycles: int = 0  # the cycles the schedule has put due since it began, whatever became of them
    missed_cycles: tuple[str, ...] = ()  # the times of those that left no record at all: a slot is missed once the next one is due


@dataclass(frozen=True)
class RyoRecord:
    """Arm R: RYO's own verdict on each candidate it read, and how each did over the same 24 hours as the agents' calls (``arm_r``)."""

    table_version: str
    table: tuple[tuple[str, str, tuple[str, ...]], ...]  # each reading, its words for the page and the verdict words it is read from
    summary: arm_r.Summary
    outcomes: tuple[arm_r.Outcome, ...]  # oldest cycle first
    cycle_times: tuple[tuple[str, str], ...]  # the time each cycle of an outcome was planned for, by cycle id


@dataclass(frozen=True)
class ComparisonLine:
    """One pair at one horizon, as the page shows it: numbers as text to four places, so two exports are the same bytes on any machine."""

    first: str
    second: str
    hours: int
    shared: int
    mean: str | None
    lower: str | None
    upper: str | None
    state: str
    since: int | None


@dataclass(frozen=True)
class Comparisons:
    alpha: str
    v_opt: str
    min_shown: int
    lines: tuple[ComparisonLine, ...]


def _four(x: float | None) -> str | None:
    """Four places, and never a negative zero (a tiny negative difference rounds to 0.0000, which has no sign)."""
    if x is None:
        return None
    text = f"{x:.4f}"
    return "0.0000" if text == "-0.0000" else text


@dataclass(frozen=True)
class Views:
    today: Today
    cycles: tuple[CycleSummary, ...]
    rows: tuple[Row, ...]
    receipts: tuple[Receipt, ...]
    record: tuple[ArmRecord, ...]
    ryo: RyoRecord | None = None  # arm R, when any cycle read RYO's verdict
    comparisons: Comparisons | None = None  # pairs of forecasters on the same opportunities (``comparison``)


def to_json(value: object) -> Any:
    """A view as plain JSON values (dataclasses to objects, tuples to lists); every number in a view is already text."""
    if isinstance(value, tuple | list):
        return [to_json(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name)) for f in fields(value)}
    return value


# --- small words -----------------------------------------------------------------------------------------------------------------------
def _at(ms: int) -> str:
    return format_rfc3339_ms(from_ms(ms))


def _planned(history: cycle.History) -> int:
    """When a cycle was planned for; every cycle history begins with a step that carries it."""
    return cast(int, history[0].step.planned_for_ms)


def percent(p: Decimal) -> str:
    """A probability as a whole percentage, half to even; never 0% or 100% for a probability that is neither."""
    whole = round(p * 100)
    return f"{min(99, max(1, whole))}%" if Decimal(0) < p < Decimal(1) else f"{whole}%"


def reason_words(code: str | None) -> str | None:
    if code is None:
        return None
    return labels.REASON_WORDS.get(code, labels.OTHER_REASON)


_ROW_STATE: Final = {
    lifecycle.State.SCHEDULED: "Deciding",
    lifecycle.State.CREATED: "Deciding",
    lifecycle.State.EVIDENCE_FROZEN: "Deciding",
    lifecycle.State.DECIDED: "Deciding",
    lifecycle.State.COMMITTED: "Open",
    lifecycle.State.AWAITING_ORIGIN: "Open",
    lifecycle.State.OPEN: "Open",
    lifecycle.State.SETTLING: "Open",
    lifecycle.State.SETTLED: "Settled",
    lifecycle.State.AMBIGUOUS: "Not settled",
    lifecycle.State.UNSETTLEABLE: "Not settled",
    lifecycle.State.VOID: "Void",
    lifecycle.State.NO_CALL: "Abstained",
    lifecycle.State.FAILED: "Failed",
    lifecycle.State.FAILED_EVIDENCE: "Failed",
    lifecycle.State.FAILED_STALE: "Failed",
    lifecycle.State.FAILED_LOST: "Failed",
    lifecycle.State.FAILED_LATE: "Too late",
}


def row_state(state: lifecycle.State) -> str:
    try:
        return _ROW_STATE[state]
    except KeyError:
        raise ViewError(f"{state.value} is not a state of a slot") from None


# --- reading the ledger ----------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Slot:
    forecast_id: str
    cycle_id: str
    arm: str
    symbol: str
    asset_id: str
    contract: str
    hours: int


def _of_type(records: Sequence[Mapping[str, Any]], name: str) -> list[dict[str, Any]]:
    return [dict(r) for r in records if r.get("type") == name]


def _clean(record: Mapping[str, Any]) -> dict[str, canonical.JsonValue]:
    return {k: v for k, v in record.items() if k != "committed_at"}


def _slots(records: Sequence[Mapping[str, Any]], protocol: Protocol) -> dict[str, _Slot]:
    """Every slot the cycles of the ledger scheduled, by id: the id is a function of the cycle, arm, asset, contract and horizon."""
    out: dict[str, _Slot] = {}
    arms_d = [name for name, _ in protocol.arms] + [name for name, _, _ in protocol.model_arms]
    arms_b = [name for name, _ in protocol.barrier_arms]
    for record in _of_type(records, "manifest"):
        body = record["body"]
        for entry in body["entries"]:
            if entry["inclusion"] != "INCLUDED":
                continue
            for contract, arms in (("D", arms_d), ("B", arms_b)):
                for arm in arms:
                    for hours in protocol.horizons_hours:
                        ms = hours * 3_600_000
                        fid = forecast.forecast_id(body["cycle_id"], arm, entry["asset_id"], contract, ms)
                        out[fid] = _Slot(fid, body["cycle_id"], arm, entry["symbol"], entry["asset_id"], contract, hours)
    return out


def _chance(f: forecast.Forecast) -> str:
    if isinstance(f, forecast.DirectionalForecast):
        return f"{percent(f.p)} up"
    return _split(f.probabilities)


def _split(parts: tuple[Decimal, Decimal, Decimal]) -> str:
    """The three chances of a barrier call as whole percentages that add up to 100, by the largest-remainder (Hamilton) method: each chance
    is rounded down, and the points still missing go one each to the chances with the largest remainders, the earlier of target, stop and
    neither on a tie. So each share shown is within one point of its exact value. As with ``percent``, a chance that is neither 0 nor 1 is
    never shown as 0% or 100%: a chance under one point takes a point before any remainder does. Two chances under a point each that come
    to a point or less together leave no such split; then each is rounded on its own, as ``percent`` does, and the row says they are rounded
    (the rules agent never gets there: its target and its stop are at least 1%)."""
    points = [Fraction(p) * 100 for p in parts]  # exact, whatever the caller's decimal context
    shown = [int(x) for x in points]  # rounded down, as no chance is below 0
    left = 100 - sum(shown)
    small = [0 < x < 1 for x in points]
    if sum(small) > left:
        target, stop, neither = (percent(p) for p in parts)
        return f"target first {target}, stop first {stop}, neither {neither} (rounded)"
    for i in sorted(range(len(points)), key=lambda j: (not small[j], shown[j] - points[j]))[:left]:  # a stable sort: on a tie, the earlier
        shown[i] += 1
    return f"target first {shown[0]}%, stop first {shown[1]}%, neither {shown[2]}%"


def _result(contract: str, settled: Settlement | None) -> str | None:
    if settled is None or settled.state.value != "SETTLED":
        return None
    if contract == "D":
        return "Rose" if settled.outcome == "1" else "Fell"
    return {"TARGET": "Target first", "STOP": "Stop first", "TIMEOUT": "Neither"}.get(str(settled.outcome))


def _reason_of(history: lifecycle.History) -> str | None:
    last = history[-1].step
    return last.reason


def _due_slots(protocol: Protocol, planned: Iterable[int], now_ms: int) -> tuple[int, ...]:
    """Every slot the schedule has put due. A ledger can hold cycles planned before the protocol's own start (a protocol written again when
    a league was restarted keeps the old cycles); the slots between the first of them and that start are due as well, on the same grid, so a
    stop in that earlier time is listed instead of being left out."""
    slots = protocol.slots_through(now_ms)
    earliest = min(planned, default=None)
    step = protocol.schedule_every_ms
    if earliest is None or earliest >= protocol.schedule_start_ms or (protocol.schedule_start_ms - earliest) % step:
        return slots
    return tuple(range(earliest, protocol.schedule_start_ms, step)) + slots


def build(records: Sequence[Mapping[str, Any]], protocol: Protocol, *, now_ms: int, scored: bool = False, recent: int = 8) -> Views:
    """Every view of a ledger at ``now_ms``."""
    histories = lifecycle.rebuild([_clean(r) for r in _of_type(records, lifecycle.TYPE)])
    cycles = cycle.rebuild([_clean(r) for r in _of_type(records, cycle.TYPE)])
    slots = _slots(records, protocol)
    calls = {r["id"]: forecast.from_record(_clean(r)) for r in _of_type(records, forecast.TYPE)}
    digests = {r["id"]: record_digest(_clean(r)) for r in _of_type(records, forecast.TYPE)}
    settlements = {
        s.forecast_id: s for s, _ in (settlement_record.from_record(_clean(r)) for r in _of_type(records, settlement_record.TYPE))
    }
    runs = {
        (b["cycle_id"], b["forecast_id"]): b
        for b in (arm_run_record.from_record(_clean(r)) for r in _of_type(records, arm_run_record.TYPE))
    }
    evidence = {r["id"]: r["body"] for r in _of_type(records, "evidence")}
    rows: list[Row] = []
    by_id: dict[str, Row] = {}
    for fid, history in histories.items():
        slot = slots.get(fid)
        if slot is None:
            raise ViewError(f"slot {fid} belongs to no cycle of this ledger and protocol")
        row = _row(slot, history, calls.get(fid), settlements.get(fid))
        rows.append(row)
        by_id[fid] = row
    planned = {cid: _planned(h) for cid, h in cycles.items()}
    due = _due_slots(protocol, planned.values(), now_ms)
    ran = set(planned.values())
    missed = tuple(_at(ms) for ms in due if ms not in ran and now_ms >= ms + protocol.schedule_every_ms)
    unknown = sorted({slot.cycle_id for slot in slots.values()} - set(cycles))
    if unknown:
        raise ViewError(f"cycle {unknown[0]} has a manifest and no history")
    order = {cid: i for i, cid in enumerate(sorted(planned, key=lambda c: planned[c]))}
    rows.sort(key=lambda r: (order[r.cycle_id], r.hours, r.symbol, r.arm, r.contract))
    read = _sources_read(records)
    summaries = tuple(
        _summary(cid, h, [r for r in rows if r.cycle_id == cid], read.get(cid, ()))
        for cid, h in sorted(cycles.items(), key=lambda kv: order[kv[0]])
    )
    if not set(calls) <= set(by_id):
        raise ViewError(f"forecast {sorted(set(calls) - set(by_id))[0]} has no history")
    unread = _unread(records)
    gap = gap_record.gaps([dict(r) for r in records])
    receipts = tuple(
        _receipt(
            by_id[fid],
            calls[fid],
            digests[fid],
            histories[fid],
            settlements.get(fid),
            runs.get((slots[fid].cycle_id, fid)),
            evidence,
            unread.get((slots[fid].cycle_id, slots[fid].asset_id), ()),
            gap[fid],
        )
        for fid in sorted(calls)
    )
    committed = [s for s in summaries if s.state == cycle.State.COMMITTED.value]
    shown = committed or list(summaries)  # a cycle that has no calls yet is never the page: the last committed one is, until another is
    latest = shown[-1] if shown else None
    latest_rows = tuple(r for r in rows if latest is not None and r.cycle_id == latest.cycle_id)
    settled = [r for r in rows if r.state == "Settled"]
    earlier = shown[:-1]
    since = earlier[-1].committed_at if earlier else None
    compared = _compared_with(earlier, rows, calls)
    previous_rows = tuple(r for r in rows if compared is not None and r.cycle_id == compared.cycle_id)
    newest = summaries[-1] if summaries else None
    running = newest.planned_for if newest is not None and newest is not latest and newest.state not in cycle.TERMINAL_WORDS else None
    today = Today(
        MODE_SCORED if scored else MODE_EXPLORATORY,
        _at(now_ms),
        latest,
        latest_rows,
        tuple(settled[-recent:]),
        since,
        _settled_since(rows, since),
        changes.PRODUCT,
        changes.FIRST_CYCLE_RULE if not earlier else changes.RULE if compared is not None else changes.NO_EARLIER_CALLS_RULE,
        _changes(latest_rows, previous_rows if compared is not None else None, calls),
        running,
        len(due),
        missed,
    )
    return Views(
        today,
        summaries,
        tuple(rows),
        receipts,
        _record(rows, histories, slots, calls, settlements, gap),
        _ryo(records, slots, calls, settlements, planned),
        _comparisons(protocol, slots, calls, settlements, histories, planned),
    )


def _comparisons(
    protocol: Protocol,
    slots: Mapping[str, _Slot],
    calls: Mapping[str, forecast.Forecast],
    settlements: Mapping[str, Settlement],
    histories: Mapping[str, lifecycle.History],
    planned: Mapping[str, int],
) -> Comparisons:
    """Every pair at every horizon, from the directional calls that settled to a rise or a fall."""
    by: dict[int, dict[str, list[comparison.Call]]] = {}
    for fid, made in calls.items():
        slot, ended = slots[fid], settlements.get(fid)
        if not isinstance(made, forecast.DirectionalForecast) or ended is None or ended.state is not SettlementState.SETTLED:
            continue
        opened = next(t.step.origin_ms for t in histories[fid] if t.step.origin_ms is not None)
        call = comparison.Call(
            slot.cycle_id, slot.asset_id, from_ms(planned[slot.cycle_id]).hour, opened + made.common.horizon_ms,
            made.p, int(ended.outcome == "1"),
        )  # fmt: skip
        by.setdefault(slot.hours, {}).setdefault(slot.arm, []).append(call)
    arms = [name for name, _ in protocol.arms]
    models = [name for name, _, _ in protocol.model_arms]
    lines = (
        comparison.compare(first, second, hours, by.get(hours, {}))
        for hours in protocol.horizons_hours
        for first, second in comparison.pairs(arms, models)
    )
    return Comparisons(
        str(comparison.ALPHA),
        str(comparison.V_OPT),
        comparison.MIN_SHOWN,
        tuple(
            ComparisonLine(x.first, x.second, x.hours, x.shared, _four(x.mean), _four(x.lower), _four(x.upper), x.state, x.since)
            for x in lines
        ),
    )


def _ryo(
    records: Sequence[Mapping[str, Any]],
    slots: Mapping[str, _Slot],
    calls: Mapping[str, forecast.Forecast],
    settlements: Mapping[str, Settlement],
    planned: Mapping[str, int],
) -> RyoRecord | None:
    """Arm R's record, or None when no cycle read RYO's verdict. Each read is settled by its cycle's 24-hour calls on the same token."""
    reads = [ryo_read.from_record(_clean(r)) for r in _of_type(records, ryo_read.TYPE)]
    if not reads:
        return None
    assets = {
        (r["body"]["cycle_id"], e["candidate_id"]): e["asset_id"] for r in _of_type(records, "manifest") for e in r["body"]["entries"]
    }
    windows: dict[tuple[str, str], list[Settlement | None]] = {}
    for fid in calls:
        slot = slots[fid]
        if slot.contract == "D" and slot.hours == ryo_read.HORIZON_HOURS:
            windows.setdefault((slot.cycle_id, slot.asset_id), []).append(settlements.get(fid))
    plans = {str(b["read_id"]): b for b in (ryo_plan.from_record(_clean(r)) for r in _of_type(records, ryo_plan.TYPE))}
    outcomes = []
    for body in reads:
        key = (str(body["cycle_id"]), str(body["candidate_id"]))
        if key not in assets or key[0] not in planned:
            raise ViewError(f"the read {ryo_read.record_id(*key)} names a candidate no cycle of this ledger had")
        outcomes.append(arm_r.outcome(body, assets[key], windows, plans.get(ryo_read.record_id(*key))))
    outcomes.sort(key=lambda o: (planned[o.cycle_id], o.symbol))
    table = tuple(  # the words of each reading in the table's own order
        (kind, ryo_read.READING_WORDS[kind], tuple(w for w, k in ryo_read.READINGS.items() if k == kind)) for kind in ryo_read.KINDS
    )
    times = tuple((cid, _at(planned[cid])) for cid in dict.fromkeys(o.cycle_id for o in outcomes))  # the outcomes are in cycle order
    return RyoRecord(ryo_read.TABLE_VERSION, table, arm_r.summary(outcomes), tuple(outcomes), times)


def _compared_with(earlier: Sequence[CycleSummary], rows: Sequence[Row], calls: Mapping[str, forecast.Forecast]) -> CycleSummary | None:
    """The cycle what changed is counted against: the latest earlier one that made at least one call. A cycle whose every slot failed
    (a source down for the whole collection) has nothing to compare, and comparing with it would say nothing changed beside new calls."""
    made = {r.cycle_id for r in rows if r.forecast_id in calls}
    for summary in reversed(earlier):
        if summary.cycle_id in made:
            return summary
    return None


HOUR_MS: Final = 3_600_000

RESULTS: Final = ("Rose", "Fell", "Target first", "Stop first", "Neither")


def _item(row: Row, call: forecast.Forecast | None) -> changes.Item:
    up = call.p if isinstance(call, forecast.DirectionalForecast) else None
    barrier = call.probabilities if isinstance(call, forecast.BarrierForecast) else None
    return changes.Item(
        row.forecast_id, row.arm, row.arm_name, row.symbol, row.hours, row.contract, row.state, up, barrier, row.result, row.reason
    )


def _changes(latest: Sequence[Row], previous: Sequence[Row] | None, calls: Mapping[str, forecast.Forecast]) -> tuple[TodayChange, ...]:
    """The top changes of the latest cycle against the one before it (``changes.rank``), as text for the page."""
    items = [_item(r, calls.get(r.forecast_id)) for r in latest]
    before = None if previous is None else [_item(r, calls.get(r.forecast_id)) for r in previous]
    return tuple(
        TodayChange(c.kind.value, c.forecast_id, c.arm_name, c.symbol, c.hours, c.sentence, c.action, str(c.size))
        for c in changes.rank(items, before)
    )


def _settled_since(rows: list[Row], since: str | None) -> tuple[tuple[str, int], ...]:
    """How the calls settled after ``since`` came out, by result, in a fixed order; none counted when there is no earlier moment."""
    if since is None:
        return ()
    late = [r.result for r in rows if r.result is not None and r.settled_at is not None and r.settled_at > since]
    return tuple((result, late.count(result)) for result in RESULTS if result in late)


def _row(slot: _Slot, history: lifecycle.History, call: forecast.Forecast | None, settled: Any) -> Row:
    state = lifecycle.current_state(history)
    level = lifecycle.current_level(history)
    origin = next((t.step.origin_ms for t in history if t.step.origin_ms is not None), None)
    settled_at = next((t.step.at_ms for t in history if t.step.state is lifecycle.State.SETTLED), None)
    committed_at = next((t.step.at_ms for t in history if t.step.state is lifecycle.State.COMMITTED), None)
    starts = origin if origin is not None else committed_at
    code = (
        _reason_of(history)
        if state in lifecycle.FAILURES or state in (lifecycle.State.NO_CALL, lifecycle.State.VOID, lifecycle.State.UNSETTLEABLE)
        else None
    )
    return Row(
        slot.forecast_id,
        slot.cycle_id,
        slot.arm,
        labels.ARM_NAMES.get(slot.arm, slot.arm),
        slot.symbol,
        slot.hours,
        slot.contract,
        row_state(state),
        None if call is None else _chance(call),
        _result(slot.contract, settled),
        reason_words(code),
        code,
        labels.trust_phrase(None if level is None or level is forecast.Level.C0 else level.value),
        None if level is None else level.value,
        None if origin is None else _at(origin),
        None if settled_at is None else _at(settled_at),
        None if starts is None else _at(starts + slot.hours * HOUR_MS),
    )


def _sources_read(records: Sequence[Mapping[str, Any]]) -> dict[str, tuple[SourceRead, ...]]:
    """For each cycle with a snapshot, what each source brought into it and for how many candidates it brought nothing (the snapshot's
    own list of what could not be read). A source is named only when it brought items or was named unread; one that is not in the
    snapshot at all is not known and is not listed."""
    source_of = {r["id"]: r["body"]["source"] for r in _of_type(records, "evidence")}
    out: dict[str, tuple[SourceRead, ...]] = {}
    for record in _of_type(records, "snapshot"):
        body = record["body"]
        ids = {i["evidence_id"] for i in body["shared"]}
        for entry in body["candidates"]:
            ids |= {i["evidence_id"] for i in entry["items"]}
        items: dict[str, int] = defaultdict(int)
        for ident in ids:
            if ident in source_of:  # an item the ledger does not hold is named as missing on the receipt and is not counted here
                items[str(source_of[ident])] += 1
        unread: dict[str, int] = defaultdict(int)
        for entry in body["candidates"]:
            for lost in entry.get("unavailable", []):
                if isinstance(lost, dict) and isinstance(lost.get("source"), str):  # a malformed member is refused by ``_unread``
                    unread[lost["source"]] += 1
        out[str(body["cycle_id"])] = tuple(SourceRead(s, items[s], unread[s]) for s in sorted(set(items) | set(unread)))
    return out


def _summary(cycle_id: str, history: cycle.History, rows: list[Row], sources: tuple[SourceRead, ...] = ()) -> CycleSummary:
    committed = next((t.step.committed_at_ms for t in history if t.step.committed_at_ms is not None), None)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.state] += 1
    return CycleSummary(
        cycle_id,
        _at(_planned(history)),
        history[-1].step.state.value,
        None if committed is None else _at(committed),
        len(rows),
        tuple((state, counts[state]) for state in labels.ROW_STATES if counts[state]),
        sources,
    )


def _evidence_line(ident: str, record: Mapping[str, Any] | None) -> EvidenceLine:
    if record is None:  # named as missing, never invented; its time is not known, so it has none (a word there is no time to read)
        return EvidenceLine(ident, "unknown", "unknown", "unknown", None, "unknown", "unknown", None, "not in this ledger")
    return EvidenceLine(
        ident,
        str(record["source"]),
        str(record["path"]),
        str(record["kind"]),
        str(record["event_time"]),
        str(record["data_mode"]),
        str(record["origin_family"]),
        None if record["raw_sha256"] is None else str(record["raw_sha256"]),
        "digest only" if record.get("redistribution") != "PUBLIC" else "shown",
    )


def _receipt(
    row: Row,
    call: forecast.Forecast,
    digest: str,
    history: lifecycle.History,
    settled: Any,
    run: Mapping[str, Any] | None,
    evidence: Mapping[str, Mapping[str, Any]],
    unread: Sequence[UnreadLine] = (),
    gap: gap_record.Gap | None = None,
) -> Receipt:
    common = call.common
    committed = next((t.step.committed_at_ms for t in history if t.step.committed_at_ms is not None), None)
    line = None
    if settled is not None:
        line = SettlementLine(
            settled.state.value,
            settled.outcome,
            settled.p0,
            settled.outcome_price,
            f"{common.oracle_primary} and {common.oracle_second}",
            settled.rule_version,
            tuple(settled.flags),
        )
    return Receipt(
        row,
        common.invalidation,
        tuple(_evidence_line(ident, evidence.get(ident)) for ident, _ in common.evidence),
        tuple(
            TimelineLine(
                labels.STEP_WORDS[t.step.state.value],
                _at(t.step.at_ms),
                reason_words(t.step.reason),
            )
            for t in history
        ),
        None if committed is None else _at(committed),
        _at(common.as_of_ms),
        f"{common.oracle_primary} and {common.oracle_second}",
        digest,
        line,
        None if run is None else _run_line(run, evidence),
        tuple(unread),
        None if gap is None or gap.age_ms is None else gap.age_ms // 1000,
        None if gap is None or gap.drift_atr is None else format(gap.drift_atr.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
    )


def _unread(records: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], tuple[UnreadLine, ...]]:
    """What each candidate's slot of each cycle's snapshot says could not be read, by (cycle, asset). A member that is not what the snapshot
    writes is refused, never shown."""
    out: dict[tuple[str, str], tuple[UnreadLine, ...]] = {}
    for record in _of_type(records, "snapshot"):
        body = record["body"]
        for entry in body["candidates"]:
            if "unavailable" not in entry:
                continue
            lines = []
            for item in entry["unavailable"]:
                source, reason, interval = (
                    (item.get(k) for k in ("source", "reason", "interval_s")) if isinstance(item, dict) else (None,) * 3
                )
                valid = (
                    isinstance(item, dict)
                    and set(item) == {"source", "reason", "interval_s"}
                    and isinstance(source, str)
                    and source
                    and reason in labels.UNREAD_WORDS
                    and (interval is None or (isinstance(interval, int) and not isinstance(interval, bool) and interval > 0))
                )
                if not valid:
                    raise ViewError(
                        f"the snapshot of cycle {body['cycle_id']} lists a source that was not read in a form that is not its own"
                    )
                lines.append(UnreadLine(str(source), str(reason), labels.UNREAD_WORDS[str(reason)], interval))
            out[(str(body["cycle_id"]), str(entry["asset_id"]))] = tuple(lines)
    return out


def _challenge(run: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> AgentChallenge | None:
    debate = run.get("debate")
    if not isinstance(debate, Mapping) or debate.get("collapsed") or debate.get("failed_role") is not None:
        return None
    said = debate.get("disagreement")
    if not isinstance(said, Mapping):
        return None
    return AgentChallenge(
        tuple(_evidence_line(ident, evidence.get(ident)) for ident in said["bear_only"]),
        tuple(said["judge_on_bear"]),
        len(said["shared"]),
        len(said["bull_only"]),
    )


def _run_line(run: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]] | None = None) -> RunLine:
    return RunLine(
        str(run["verdict"]),
        reason_words(run["reason"]),
        str(run["gate"]),
        None if run["proposed_p"] is None else percent(Decimal(run["proposed_p"])),
        None if run["final_p"] is None else percent(Decimal(run["final_p"])),
        int(run["calls"]),
        int(run["tokens"]),
        int(run["cutoff_ms"]) // 1000,
        None if evidence is None else _challenge(run, evidence),
    )


def _known_outcomes(
    groups: Mapping[tuple[str, str], Sequence[str]],
    calls: Mapping[str, forecast.Forecast],
    settlements: Mapping[str, Any],
    histories: Mapping[str, lifecycle.History],
) -> dict[int, list[tuple[int, int]]]:
    """For each horizon, when each settled directional call's outcome became known (the time of its last step) and what it was, over every
    arm: the history a climatological rate is read from. A call that did not settle on a rise or not has no outcome to count."""
    known: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for (_, contract), members in groups.items():
        if contract != "D":
            continue
        for i in members:
            if i in calls and i in settlements and settlements[i].state.value == "SETTLED" and _scored_outcome("D", settlements[i]):
                known[calls[i].common.horizon_ms].append((histories[i][-1].step.at_ms, int(settlements[i].outcome)))
    return known


def _record(
    rows: list[Row],
    histories: Mapping[str, lifecycle.History],
    slots: Mapping[str, _Slot],
    calls: Mapping[str, forecast.Forecast],
    settlements: Mapping[str, Any],
    gaps: Mapping[str, gap_record.Gap],
) -> tuple[ArmRecord, ...]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fid in histories:
        slot = slots[fid]
        groups[(slot.arm, slot.contract)].append(fid)
    out = []
    cuts = _gap_cuts(groups, calls, settlements, gaps)
    known = _known_outcomes(groups, calls, settlements, histories)
    for (arm, contract), ids in sorted(groups.items()):
        den = lifecycle.denominator([histories[i] for i in ids])
        cov = scoring.coverage(*den.coverage_inputs())
        settled = [
            i
            for i in ids
            if contract in SCORED
            and i in calls
            and i in settlements
            and settlements[i].state.value == "SETTLED"
            and _scored_outcome(contract, settlements[i])
        ]
        every = [_own_brier(contract, calls[i], settlements[i]) for i in settled]
        # a call counts towards the score only if it reached C3 by its origin and still holds C3 or more (contract sections 8 and 17): the
        # single eligibility service decides (INVARIANTS S-1); the score of every settled call and the share that missed C3 stand beside it
        scores = [every[k] for k, i in enumerate(settled) if lifecycle.headline_eligible(histories[i])]
        missed = None if not every else percent(Decimal(len(every) - len(scores)) / len(every))
        headline = _mean(scores)
        log_score: str | None = None
        climate_skill: str | None = None
        climate_n = 0
        reliability: tuple[ReliabilityLine, ...] = ()
        bounds: BoundsLine | None = None
        if contract == "D":
            counted = [i for i in settled if lifecycle.headline_eligible(histories[i])]
            directional = [
                scoring.Directional(
                    i,
                    arm,
                    calls[i].common.symbol,
                    "all",
                    cast(forecast.DirectionalForecast, calls[i]).p,
                    int(settlements[i].outcome),
                    # the share of rises among outcomes known in the 30 days before the evidence time, never later data
                    scoring.climatology(known[calls[i].common.horizon_ms], calls[i].common.as_of_ms),
                )
                for i in counted
            ]
            log_score, climate_skill, climate_n, reliability = _more_scores(directional)
            bounds = _bounds(
                directional,
                [
                    scoring.ExcludedDirectional(
                        i, arm, calls[i].common.symbol, "all", str(settlements[i].reason), cast(forecast.DirectionalForecast, calls[i]).p
                    )
                    for i in ids
                    if i in calls and i in settlements and settlements[i].state.value in ("VOID", "UNSETTLEABLE")
                ],
            )
        not_scored = _exclusions(
            str(settlements[i].reason) for i in ids if i in settlements and settlements[i].state.value in ("VOID", "UNSETTLEABLE")
        )
        out.append(
            ArmRecord(
                arm,
                labels.ARM_NAMES.get(arm, arm),
                contract,
                den.scheduled,
                den.forecasts,
                den.settled,
                den.pending,
                den.ambiguous + den.void + den.unsettleable,
                den.no_calls,
                den.failures,
                den.in_flight,
                None if cov.coverage is None else percent(cov.coverage),
                None if cov.availability is None else percent(cov.availability),
                headline,
                len(scores),
                _mean(every),
                len(every),
                missed,
                _label(len(every)),
                _skill(contract, scores),
                _strata(dict(zip(settled, every, strict=True)), gaps, cuts.get(contract, {})),
                log_score,
                climate_skill,
                climate_n,
                reliability,
                not_scored,
                bounds,
            )
        )
    return tuple(out)


_SPLITS: Final = (
    ("age", lambda g: None if g.age_ms is None else Decimal(g.age_ms) / 60_000),  # minutes
    ("drift", lambda g: g.drift_atr),  # ATR
)


def _gap_cuts(
    groups: Mapping[tuple[str, str], Sequence[str]],
    calls: Mapping[str, forecast.Forecast],
    settlements: Mapping[str, Any],
    gaps: Mapping[str, gap_record.Gap],
) -> dict[str, dict[str, tuple[Decimal, Decimal]]]:
    """For each scored contract and each split, the two values that cut the settled calls of every arm of the contract into thirds: the
    same cuts for every arm, so the groups are comparable. A split with too few calls has none."""
    out: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    for contract in SCORED:
        ids = [
            i
            for (_, c), members in groups.items()
            if c == contract
            for i in members
            if i in calls and i in settlements and settlements[i].state.value == "SETTLED" and _scored_outcome(contract, settlements[i])
        ]
        for name, pick in _SPLITS:
            found = [v for i in ids if i in gaps and (v := pick(gaps[i])) is not None]
            cut = gap_record.tercile_cuts(found)
            if cut is not None:
                out.setdefault(contract, {})[name] = (Decimal(cut[0]), Decimal(cut[1]))
    return out


def _strata(
    scored: Mapping[str, Decimal], gaps: Mapping[str, gap_record.Gap], cuts: Mapping[str, tuple[Decimal, Decimal]]
) -> tuple[StratumLine, ...]:
    """An arm's settled calls in thirds by each split that has cuts: how many are in each, their range, their mean score."""
    lines: list[StratumLine] = []
    for name, pick in _SPLITS:
        if name not in cuts:
            continue
        members: list[list[tuple[Decimal, Decimal]]] = [[], [], []]
        for ident, score_ in scored.items():
            value = pick(gaps[ident]) if ident in gaps else None
            if value is not None:
                members[gap_record.stratum(value, cuts[name])].append((value, score_))
        for group, found in enumerate(members):
            values = [v for v, _ in found]
            lines.append(
                StratumLine(
                    name,
                    group,
                    None if not values else format(min(values).quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
                    None if not values else format(max(values).quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
                    len(found),
                    _mean([sc for _, sc in found]),
                )
            )
    return tuple(lines)


SCORED: Final = ("D", "B")  # the contracts that carry a probability score; P and A are not forecasts of an event
REFERENCE_BRIER: Final = {"D": Decimal("0.25"), "B": Decimal(2) / Decimal(3)}  # a coin; three equal parts


def _scored_outcome(contract: str, settlement: Any) -> bool:
    """A settled call is scored on the outcomes its event has: a rise or not for D; target, stop or timeout for B (ambiguity is a rate)."""
    return settlement.outcome in (("0", "1") if contract == "D" else scoring.BARRIER_CLASSES)


def _own_brier(contract: str, call: forecast.Forecast, settlement: Any) -> Decimal:
    """The score of a settled call on its own event: the Brier score of a rise for D; for B the three-class one (target, stop, timeout)."""
    if contract == "D":
        return scoring.brier(cast(forecast.DirectionalForecast, call).p, int(settlement.outcome))
    barrier = cast(forecast.BarrierForecast, call)
    return scoring.brier3((barrier.p_target, barrier.p_stop, barrier.p_timeout), str(settlement.outcome))


MIN_FOR_RELIABILITY: Final = 9  # directional calls before the reliability groups are shown: three in each of three groups
RELIABILITY_GROUPS: Final = 3


def _more_scores(
    rows: Sequence[scoring.Directional],
) -> tuple[str | None, str | None, int, tuple[ReliabilityLine, ...]]:
    """The logarithmic score, the skill against the climatological rate and the reliability groups of directional calls that settled."""
    if not rows:
        return None, None, 0, ()
    summary = scoring.summarize_directional(rows)
    climate = summary.brier_vs_climate
    groups: tuple[ReliabilityLine, ...] = ()
    if len(rows) >= MIN_FOR_RELIABILITY:
        groups = tuple(
            ReliabilityLine(
                n,
                b.count,
                format(b.mean_p.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
                format(b.frequency.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
                format(b.low.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
                format(b.high.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
            )
            for n, b in enumerate(scoring.reliability_bins(rows, RELIABILITY_GROUPS))
        )
    return (
        format(summary.log_loss.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
        None if climate is None else format(climate.skill.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f"),
        0 if climate is None else climate.n,
        groups,
    )


def _bounds(rows: Sequence[scoring.Directional], excluded: Sequence[scoring.ExcludedDirectional]) -> BoundsLine | None:
    """The Brier score and its skill against a coin, with every excluded call counted as the best and as the worst it could have been."""
    if not rows or not excluded:
        return None
    sensitivity = scoring.directional_sensitivity(rows, excluded)

    def text(value: Decimal) -> str:
        return format(value.quantize(PLACES, rounding=ROUND_HALF_EVEN), "f")

    return BoundsLine(
        len(excluded),
        text(sensitivity.brier.lower),
        text(sensitivity.brier.upper),
        text(sensitivity.brier_vs_half.lower),
        text(sensitivity.brier_vs_half.upper),
        sensitivity.brier_vs_half.changes_sign,
    )


def _exclusions(reasons: Iterable[str]) -> tuple[ExclusionLine, ...]:
    return tuple(ExclusionLine(reason, reason_words(reason) or reason, n) for reason, n in sorted(Counter(reasons).items()))


def _skill(contract: str, scores: Sequence[Decimal]) -> str | None:
    """How far the mean score is below the score of the fixed reference, as a share of that score; none for no scores."""
    if not scores:
        return None
    mean = sum(scores, Decimal(0)) / len(scores)
    return format((1 - mean / REFERENCE_BRIER[contract]).quantize(PLACES, rounding=ROUND_HALF_EVEN), "f")


def _mean(scores: Sequence[Decimal]) -> str | None:
    """The mean of the scores to four places, half to even, as text; none for no scores."""
    if not scores:
        return None
    return format((sum(scores, Decimal(0)) / len(scores)).quantize(PLACES, rounding=ROUND_HALF_EVEN), "f")


def _label(n: int) -> str:
    if n == 0:
        return "No settled calls yet"
    return f"Provisional: {n} settled {'call' if n == 1 else 'calls'}; nothing is ranked"
