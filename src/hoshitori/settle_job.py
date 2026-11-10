"""Opening, closing and settling the forecasts that are waiting: one pass over the slots, as often as the scheduler likes.

``CYCLE_RUNNER_SPEC`` section 3 and contract sections 7 and 17 are the rules. A slot that is waiting for its origin opens
when the origin has come, one that is open starts settling when its horizon has passed, and one that is settling is settled
from the two oracles' candles. Each of those is a step of the slot's own history, so a pass can be repeated or interrupted
and gives the same records. A pass that stopped after a slot's settlement record and before the step that ends the slot is
finished by a later one: given that record (``Waiting.settlement``), it ends the slot with it as it is and writes nothing
else for the slot.

* **Candles are read through the gateway, once for each instrument and minute in a pass.** Each oracle's venue is asked for
  its time first: a bar counts only if it had closed by that time, and without it nothing can be said, so a slot whose venue
  gave no time waits. It waits as long as a missing bar does: once the retry window has passed, a venue that still gives no
  time has none of its bars read, and the slot is ``UNSETTLEABLE_MISSING``.
* **Nothing is filled in.** A bar that is not there is missing, and the settlement says ``PENDING`` until the retry window of
  an hour has passed since settling began; then it is ``UNSETTLEABLE_MISSING``. A token that no longer has an oracle pair in
  the registry is treated the same way and, when the window has passed, is void as delisted.
* **A pass writes only new records.** The settlement record names the forecast record it settles by digest; the slot's step
  names the settlement. A forecast is never edited, and a settlement the pass is handed as already written is not written
  again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from typing import Any, Final, cast

from hoshitori import canonical, collect, lifecycle, oracle, settlement, settlement_record
from hoshitori.capture import RawCapture
from hoshitori.collect import UNREADABLE, Environment, Problem, answer
from hoshitori.forecast import BarrierForecast, Forecast
from hoshitori.gateway import Call
from hoshitori.governor import Priority
from hoshitori.protocol import Protocol
from hoshitori.settlement import Bar, Oracle, OraclePair, Settlement, SettlementError, State
from hoshitori.settlement import Barrier as SettlementBarrier
from hoshitori.timeline import MINUTE_MS, TimelineError, from_ms, origin, outcome_candle_open, reference_candle_open, to_ms
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_base import VenueAdapter
from hoshitori.venue_data import Candle, CandleSeries

MAX_WINDOW_BARS: Final = 300  # the bars one request asks for; every venue's window holds at least this many
RETRY_WINDOW_MS: Final = 3_600_000  # a missing bar is looked for again, at every pass, for an hour after settling began


@dataclass(frozen=True)
class SettleServices:
    protocol: Protocol
    env: Environment
    source_urn: str


class SettleJobError(ValueError):
    """A slot whose records do not agree with each other."""


class _Status(Enum):
    DEFERRED = auto()  # a venue gave no time, and the retry window is still open
    PENDING = auto()  # not decidable yet
    ENDED = auto()  # settled, void or unsettleable


@dataclass(frozen=True)
class Waiting:
    """A forecast and where its slot is."""

    forecast: Forecast
    digest: str  # of the forecast record, which a settlement names
    history: lifecycle.History
    settlement: dict[str, canonical.JsonValue] | None = None  # the slot's settlement record in the ledger, if a pass wrote one


@dataclass(frozen=True)
class Passed:
    records: tuple[dict[str, canonical.JsonValue], ...]  # new records, in order, for the ledger
    histories: Mapping[str, lifecycle.History]  # the histories of the slots that moved
    pending: tuple[str, ...]  # settling, and not decidable yet
    deferred: tuple[str, ...]  # settling, and not tried: a venue gave no time, and the retry window is still open
    failed: tuple[tuple[str, str], ...] = ()  # (slot, reason): a slot that could not be moved or settled, left where it was


def _bar(candle: Candle) -> Bar | None:
    try:
        volume = None if candle.volume is None else Decimal(candle.volume)
        return Bar(to_ms(candle.open_time), Decimal(candle.open), Decimal(candle.high), Decimal(candle.low), Decimal(candle.close), volume)
    except (SettlementError, InvalidOperation):
        return None


def _step(history: lifecycle.History, state: lifecycle.State, at_ms: int, **members: Any) -> lifecycle.History:
    return lifecycle.advance(history, lifecycle.Step(state, at_ms, **members))


def _ending(history: lifecycle.History, ended: Settlement, at_ms: int) -> lifecycle.History:
    """The history after the step that ends the slot with its settlement: it names the settlement, and a void or unsettleable one also
    carries its reason."""
    members: dict[str, Any] = {"settlement_id": ended.settlement_id}
    if ended.state in (State.VOID, State.UNSETTLEABLE):
        members["reason"] = ended.reason
    return _step(history, _ENDS[ended.state], at_ms, **members)


def _recorded(slot: Waiting) -> Settlement:
    """The settlement an earlier pass wrote for the slot, read back as strictly as it was written. It must name the slot's forecast record,
    or the step that ends the slot would name another forecast's settlement."""
    ended, named = settlement_record.from_record(cast(dict[str, canonical.JsonValue], slot.settlement))
    if ended.forecast_id != slot.forecast.common.forecast_id or named != slot.digest:
        raise SettleJobError("a slot's settlement record names the slot's forecast record by its id and digest")
    return ended


def settle_pass(sv: SettleServices, waiting: Sequence[Waiting], *, wait_s: float = 30.0, earmark: int | None = None) -> Passed:
    """Move every slot as far as the clock allows, and settle the ones that can be settled."""
    now = collect.now_ms(sv.env.clock)
    records: list[dict[str, canonical.JsonValue]] = []
    moved: dict[str, lifecycle.History] = {}
    settling: list[Waiting] = []
    for slot in waiting:
        history = slot.history
        horizon = slot.forecast.common.horizon_ms
        state = lifecycle.current_state(history)
        if state is lifecycle.State.AWAITING_ORIGIN:
            opens = origin(cast(int, history[-1].step.eligible_after_ms))
            if now >= opens:
                history = _step(history, lifecycle.State.OPEN, now, origin_ms=opens)
                state = lifecycle.State.OPEN
        if state is lifecycle.State.OPEN:
            opened = cast(int, history[-1].step.origin_ms)
            if now >= opened + horizon:
                history = _step(history, lifecycle.State.SETTLING, now)
                state = lifecycle.State.SETTLING
        if state is lifecycle.State.SETTLING and slot.settlement is not None:
            history = _ending(history, _recorded(slot), now)  # its settlement is written already: it is used as it is, never made again
        elif state is lifecycle.State.SETTLING:
            settling.append(Waiting(slot.forecast, slot.digest, history))
        if history is not slot.history:
            records.extend(lifecycle.to_record(t, sv.source_urn, t.step.at_ms) for t in history[len(slot.history) :])
            moved[slot.forecast.common.forecast_id] = history
    pending: list[str] = []
    deferred: list[str] = []
    failed: list[tuple[str, str]] = []
    if settling:
        needs = _needs(sv, settling, now)
        read = _read(sv, needs, wait_s, earmark)
        for slot in settling:
            fid = slot.forecast.common.forecast_id
            try:
                status, outcome = _decide(slot, now, needs[fid], read)
            except (SettleJobError, SettlementError, TimelineError, InvalidOperation, ValueError, KeyError) as exc:
                failed.append((fid, str(exc)[:200]))  # one slot that cannot be decided must not stop the settlement of the others
                continue
            if status is _Status.DEFERRED:
                deferred.append(fid)
            elif status is _Status.PENDING:
                pending.append(fid)
            else:
                ended = cast(Settlement, outcome)
                records.append(settlement_record.to_record(ended, slot.digest, sv.source_urn, now))
                history = moved.get(fid, slot.history)
                after = _ending(history, ended, now)
                records.extend(lifecycle.to_record(t, sv.source_urn, t.step.at_ms) for t in after[len(history) :])
                moved[fid] = after
    return Passed(tuple(records), moved, tuple(pending), tuple(deferred), tuple(failed))


_ENDS: Final = {
    State.SETTLED: lifecycle.State.SETTLED,
    State.AMBIGUOUS: lifecycle.State.AMBIGUOUS,
    State.VOID: lifecycle.State.VOID,
    State.UNSETTLEABLE: lifecycle.State.UNSETTLEABLE,
}


# --- reading the oracles ----------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Need:
    """What one slot needs: the two oracle instruments and, for each, the minutes it must have a bar for."""

    pair: oracle.Pick | None
    origin_ms: int
    primary_minutes: tuple[int, ...]
    second_minutes: tuple[int, ...]
    basis: tuple[str, str] | None = None  # the (venue, instrument) that prices the stable coin in dollars, if the registry lists one
    basis_minute: int | None = None  # the bar of the outcome minute it is read for


@dataclass(frozen=True)
class Read:
    times: Mapping[str, datetime | None]  # each venue's own clock, if it told it
    bars: Mapping[tuple[str, str, int], Bar | None]  # (venue, instrument, minute) -> the closed bar, if there is one


def _minutes(made: Forecast, opened: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The minutes (bar opens) each oracle is read for. A directional forecast needs the reference and outcome bars of both; a barrier
    forecast the reference bar of both and, of the primary, every bar of its horizon."""
    reference = reference_candle_open(opened)
    if isinstance(made, BarrierForecast):
        window = tuple(opened + k * MINUTE_MS for k in range(made.common.horizon_ms // MINUTE_MS))
        return (reference, *window), (reference,)
    both = (reference, outcome_candle_open(opened, made.common.horizon_ms))
    return both, both


def _needs(sv: SettleServices, settling: Sequence[Waiting], now: int) -> dict[str, _Need]:
    needs: dict[str, _Need] = {}
    for slot in settling:
        common = slot.forecast.common
        opened = cast(int, next(t.step.origin_ms for t in slot.history if t.step.state is lifecycle.State.OPEN))
        pair = oracle.pick_sealed(
            sv.env.registry, sv.protocol, common.asset_id, from_ms(common.as_of_ms), from_ms(opened + common.horizon_ms)
        )
        primary, second = _minutes(slot.forecast, opened)
        end = opened + common.horizon_ms
        basis = oracle.stable_basis_instrument(sv.env.registry, sv.env.adapters, from_ms(end))
        needs[common.forecast_id] = _Need(pair, opened, primary, second, basis, outcome_candle_open(opened, common.horizon_ms))
    return needs


def _runs(minutes: Sequence[int]) -> list[list[int]]:
    """The minutes in order, split into runs of consecutive minutes of at most ``MAX_WINDOW_BARS``."""
    runs: list[list[int]] = []
    for minute in sorted(set(minutes)):
        if runs and minute == runs[-1][-1] + MINUTE_MS and len(runs[-1]) < MAX_WINDOW_BARS:
            runs[-1].append(minute)
        else:
            runs.append([minute])
    return runs


def _read(sv: SettleServices, needs: Mapping[str, _Need], wait_s: float, earmark: int | None) -> Read:
    wanted: dict[tuple[str, str], list[int]] = {}  # an ordered map: the order in which the slots first need each instrument
    for need in needs.values():
        if need.pair is not None:
            wanted.setdefault((need.pair.primary.venue, need.pair.primary.raw_id), []).extend(need.primary_minutes)
            wanted.setdefault((need.pair.second.venue, need.pair.second.raw_id), []).extend(need.second_minutes)
            if need.basis is not None and need.basis_minute is not None:  # the stable coin's dollar price at the outcome minute
                wanted.setdefault(need.basis, []).append(need.basis_minute)
    return read_bars(sv.env, wanted, wait_s, earmark, "settle")


def read_bars(env: Environment, wanted: Mapping[tuple[str, str], Sequence[int]], wait_s: float, earmark: int | None, purpose: str) -> Read:
    """Every venue's time, and every (venue, instrument, minute) wanted, each asked once, in windows of consecutive minutes.

    ``purpose`` names the pass that asks, in the gateway's records."""
    deadline = env.clock.now() + timedelta(seconds=wait_s)

    def call(venue: str, operation: str, args: Mapping[str, object]) -> Call:
        return Call(env.adapters[venue].provider, operation, args, Priority.P0, deadline, purpose, window=earmark)

    asked = {venue: env.runner.submit(call(venue, "time", {})) for venue in dict.fromkeys(v for v, _ in wanted) if venue in env.adapters}
    times: dict[str, datetime | None] = {}
    for venue, future in asked.items():
        times[venue] = _told(env.adapters[venue], answer(env.captures, future, wait_s))
    bars: dict[tuple[str, str, int], Bar | None] = {}
    windows = []
    for (venue, raw), minutes in wanted.items():
        told = times.get(venue)
        if told is None:
            continue
        adapter = env.adapters[venue]
        for run in _runs(minutes):
            args = adapter.normalise(
                "candles",
                {
                    "instrument": raw,
                    "interval_s": 60,
                    "start": format_rfc3339_ms(from_ms(run[0])),
                    "end": format_rfc3339_ms(from_ms(run[-1] + MINUTE_MS)),
                },
            )
            windows.append((venue, raw, run, told, env.runner.submit(call(venue, "candles", args))))
    for venue, raw, run, told, future in windows:
        capture = answer(env.captures, future, wait_s)
        for minute in run:
            bars[(venue, raw, minute)] = None if isinstance(capture, Problem) else _closed_bar(env.adapters[venue], capture, told, minute)
    return Read(times, bars)


DAY_MS: Final = 86_400_000


def read_daily_closes(
    env: Environment, wanted: Mapping[tuple[str, str], str], now_ms: int, days: int, wait_s: float, purpose: str
) -> dict[str, dict[int, Decimal]]:
    """The closes of the last ``days`` finished UTC days of each (venue, instrument), by the symbol it is wanted for, each asked once.

    The day that is not over is never asked for. A venue that gave no time, or an answer that cannot be read, leaves its symbol out: the
    caller reads a symbol with no closes as one whose behaviour is not known, never as one that does not move."""
    deadline = env.clock.now() + timedelta(seconds=wait_s)

    def call(venue: str, operation: str, args: Mapping[str, object]) -> Call:
        return Call(env.adapters[venue].provider, operation, args, Priority.P0, deadline, purpose)

    asked = {venue: env.runner.submit(call(venue, "time", {})) for venue in dict.fromkeys(v for v, _ in wanted) if venue in env.adapters}
    times = {venue: _told(env.adapters[venue], answer(env.captures, future, wait_s)) for venue, future in asked.items()}
    today = now_ms // DAY_MS * DAY_MS
    windows = []
    for (venue, raw), symbol in wanted.items():
        told = times.get(venue)
        if told is None:
            continue
        args = env.adapters[venue].normalise(
            "candles",
            {
                "instrument": raw,
                "interval_s": DAY_MS // 1000,
                "start": format_rfc3339_ms(from_ms(today - days * DAY_MS)),
                "end": format_rfc3339_ms(from_ms(today)),
            },
        )
        windows.append((venue, symbol, told, env.runner.submit(call(venue, "candles", args))))
    out: dict[str, dict[int, Decimal]] = {}
    for venue, symbol, told, future in windows:
        capture = answer(env.captures, future, wait_s)
        if isinstance(capture, Problem):
            continue
        try:
            series = cast(CandleSeries, env.adapters[venue].parse(capture, told))
        except UNREADABLE:
            continue
        out[symbol] = {to_ms(c.open_time): Decimal(c.close) for c in series.candles if to_ms(c.open_time) < today}
    return out


def _told(adapter: VenueAdapter, capture: RawCapture | Problem) -> datetime | None:
    """The venue's own clock from its answer, or ``None`` if there was no answer or it cannot be read."""
    if isinstance(capture, Problem):
        return None
    try:
        return cast(datetime, adapter.parse(capture))
    except UNREADABLE:
        return None


def _closed_bar(adapter: VenueAdapter, capture: RawCapture, told: datetime, minute: int) -> Bar | None:
    try:
        series = cast(CandleSeries, adapter.parse(capture, told))
    except UNREADABLE:
        return None
    candle = series.at(from_ms(minute))
    return None if candle is None else _bar(candle)


def _basis(need: _Need, read: Read) -> Decimal | None:
    """How far the stable coin was from one dollar at the outcome minute (its dollar price less one), if that bar was read; else none. A
    basis that was not read is not known: it never stands for a basis of zero, and only a read basis can mark a settlement."""
    if need.basis is None or need.basis_minute is None:
        return None
    bar = read.bars.get((need.basis[0], need.basis[1], need.basis_minute))
    return None if bar is None else bar.close - 1


DEFERRED: Final = "DEFERRED"


def oracle_pair(
    pick: oracle.Pick, read: Read, pair_id: str, minutes: tuple[Sequence[int], Sequence[int]], *, exhausted: bool
) -> OraclePair | str:
    """The oracle pair a settlement reads, built from the bars a pass read for the pick's two instruments; or why there is none:
    ``DEFERRED`` (a venue gave no time and the retry window is still open) or ``VOID_OUTAGE`` (the retry window has passed and the venue
    still gives no time: it was out for the whole window). Each oracle holds the bars of its own ``minutes`` (primary, then second) that
    the pass read; a minute it did not read is missing, never filled in."""
    oracles = []
    for instrument, wanted in ((pick.primary, minutes[0]), (pick.second, minutes[1])):
        told = read.times.get(instrument.venue)
        if told is None:
            return "VOID_OUTAGE" if exhausted else DEFERRED
        bars = {m: bar for m in wanted if (bar := read.bars.get((instrument.venue, instrument.raw_id, m))) is not None}
        tick = Decimal(instrument.tick_size) if instrument.tick_size is not None else None
        oracles.append(
            Oracle(instrument.venue, instrument.raw_id, instrument.quote_class, Decimal(instrument.multiplier), tick, to_ms(told), bars)
        )
    return OraclePair(pair_id, oracles[0], oracles[1])


def _decide(slot: Waiting, now: int, need: _Need, read: Read) -> tuple[_Status, Settlement | None]:
    """What can be said of a settling slot now: nothing (a venue gave no time, and the retry window is still open), not yet, or its
    settlement."""
    common = slot.forecast.common
    began = next(t.step.at_ms for t in slot.history if t.step.state is lifecycle.State.SETTLING)
    exhausted = now - began >= RETRY_WINDOW_MS
    if need.pair is None:  # no oracle pair is known for the token: look again until the window has passed, then it is void as delisted
        return (
            (_Status.ENDED, settlement.void(common.forecast_id, common.contract, common.oracle_pair, "VOID_DELISTED"))
            if exhausted
            else (_Status.PENDING, None)
        )
    pair = oracle_pair(need.pair, read, common.oracle_pair, (need.primary_minutes, need.second_minutes), exhausted=exhausted)
    if pair == DEFERRED:
        return _Status.DEFERRED, None
    if isinstance(pair, str):
        return _Status.ENDED, settlement.void(common.forecast_id, common.contract, common.oracle_pair, pair)
    basis = _basis(need, read)
    wanted = slot.forecast.settlement_input(need.origin_ms)
    if isinstance(wanted, SettlementBarrier):
        outcome = settlement.settle_barrier(wanted, pair, retries_exhausted=exhausted, basis=basis)
    else:
        outcome = settlement.settle_directional(wanted, pair, retries_exhausted=exhausted, basis=basis)
    return (_Status.PENDING, None) if outcome.state is State.PENDING else (_Status.ENDED, outcome)
