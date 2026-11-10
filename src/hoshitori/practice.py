"""The practice decisions of the rules arm (``SIMULATOR_SPEC``; contract section 4): what to buy, sell and mark now, from the
ledger and the bars.

``plan`` is a pure function of the ledger's records, the configuration, the oracle's one-minute bars and the time; it returns
the records to append and what it is still waiting for. Nothing is entered twice: a purchase and a sale are one record per
book and forecast.

* **Books.** One per cost level (base, stress, double fees) for the arm, opened with a fixed equity. A book is the fold of
  its practice records, read back with ``practice_record.from_record``.
* **Entries.** A committed directional forecast of the arm is a LONG decision when its probability is at least one half plus
  the arm's edge. Once the bar it would be filled in has closed, it is a purchase request of ``request_weight`` of the book's
  equity. The requests of one pass are put through the risk kernel together, with the positions already held (at their cost)
  as the base: what the kernel leaves above them is shared among the new requests in proportion; the ladder (halved, flat,
  halted) lets nothing new through. The purchase is filled by ``fills.entry`` and refused by the book if it cannot be paid in
  cash.
* **Exits.** Stop and target lie ``k x ATR14`` (the ATR in the forecast's features evidence) below and above the fill price,
  the same multipliers as the barrier forecast; the sale is by ``fills.exit_order``: the stop, the target or the end of the
  horizon.
* **Marks.** A mark at the newest closed bar of each token held, so that the ladder sees the drawdown.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Any, Final, cast

from hoshitori import book, canonical, fills, forecast, lifecycle, practice_record, risk_kernel
from hoshitori.barrier_rates import K_STOP, K_TARGET
from hoshitori.evidence import record_digest
from hoshitori.fills import LEVELS, Costs, Kind, Status
from hoshitori.risk_kernel import Mode
from hoshitori.rules_arm import EDGE
from hoshitori.settlement import Bar
from hoshitori.timeline import origin

ARM: Final = "L0"
REQUEST_WEIGHT: Final = Decimal("0.02")  # of the book's equity, for each LONG forecast (a prior)
OPENING_EQUITY: Final = Decimal(100_000)
RETRY_MS: Final = 3_600_000  # a bar that is still missing an hour after it should have been there is missing for good
_SIX: Final = Decimal("1E-6")
_EIGHT: Final = Decimal("1E-8")
_CTX: Final = Context(prec=60, rounding=ROUND_HALF_EVEN)


class PracticeError(ValueError):
    """A configuration or a ledger the plan cannot work from."""


@dataclass(frozen=True)
class Config:
    arm: str = ARM
    levels: tuple[str, ...] = LEVELS
    equity: Decimal = OPENING_EQUITY
    request_weight: Decimal = REQUEST_WEIGHT
    limits: risk_kernel.Limits = field(default_factory=risk_kernel.Limits)
    costs: Costs = field(default_factory=Costs)
    # A shadow book (contract section 5): it follows the long calls of ``follows`` (the reference rule) on the slots where ``passed_by``
    # chose to make no call, at the same size and costs. It is a hypothetical book beside the arm's own, never part of it.
    follows: str | None = None
    passed_by: str | None = None

    def __post_init__(self) -> None:
        if not self.arm or not self.levels or len(set(self.levels)) != len(self.levels) or not set(self.levels) <= set(LEVELS):
            raise PracticeError(f"a practice run has an arm and some of the cost levels {LEVELS}, once each")
        if not isinstance(self.equity, Decimal) or not self.equity.is_finite() or self.equity <= 0:
            raise PracticeError("the opening equity is a positive decimal")
        if not isinstance(self.request_weight, Decimal) or not 0 < self.request_weight <= 1:
            raise PracticeError("a request is a share of equity above zero and at most one")
        if (self.follows is None) != (self.passed_by is None) or (self.follows is not None and self.follows == self.passed_by):
            raise PracticeError("a shadow book names the arm it follows and a different arm whose passes it follows")


def config_for(arm: str, terms: Mapping[str, Decimal]) -> Config:
    """The configuration of an arm's books from the terms a protocol records (``Protocol.practice_values``)."""
    return Config(
        arm=arm,
        equity=terms["equity"],
        request_weight=terms["request_weight"],
        limits=risk_kernel.Limits(*(terms[k] for k in ("per_token", "gross", "net", "cluster", "halve_at", "flat_at", "halt_at"))),
        costs=Costs(terms["fee_bps"], terms["slip_bps"], terms["stress_slip_bps"], int(terms["latency_ms"])),
    )


@dataclass(frozen=True)
class Plan:
    records: tuple[dict[str, canonical.JsonValue], ...]  # to append, in order
    waiting: tuple[str, ...]  # what needs a bar that is not there yet, as "book/forecast/entry" or "book/forecast/exit"
    missed: tuple[str, ...]  # what can no longer be done: the bar is missing and the retries are over, or a held position has no forecast
    refused: tuple[str, ...]  # purchases the book could not take, as "book/forecast"


@dataclass(frozen=True)
class _Due:
    forecast_id: str
    digest: str
    asset_id: str
    symbol: str
    eligible_after_ms: int
    t0_ms: int
    horizon_ms: int
    atr: Decimal
    ended: bool  # the forecast was voided or could not be settled: no new purchase, but a purchase already made is still sold

    def fill_open_ms_with(self, latency_ms: int) -> int:
        """The open of the bar a purchase for this forecast is filled in."""
        return fills.first_bar_ms(fills.earliest_fill_ms(self.eligible_after_ms, self.t0_ms, latency_ms))


def _decimal(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _clean(record: Mapping[str, Any]) -> dict[str, canonical.JsonValue]:
    return cast(dict[str, canonical.JsonValue], {k: v for k, v in record.items() if k != "committed_at"})


def _longs(records: Sequence[Mapping[str, Any]], cfg: Config) -> dict[str, _Due]:
    """The directional forecasts of the arm that are a LONG decision and whose times and ATR are known, by forecast id. For a shadow book,
    the long forecasts of the arm it follows on the slots that the other arm passed."""
    histories = lifecycle.rebuild([_clean(r) for r in records if r.get("type") == lifecycle.TYPE])
    atr_of = {r["id"]: _decimal(r["body"].get("fields", {}).get("atr14")) for r in records if r.get("type") == "evidence"}
    out: dict[str, _Due] = {}
    for record in records:
        if record.get("type") != forecast.TYPE:
            continue
        made = forecast.from_record(_clean(record))
        followed = cfg.follows or cfg.arm
        if not isinstance(made, forecast.DirectionalForecast) or made.common.arm_id != followed or made.p < Decimal("0.5") + EDGE:
            continue
        if cfg.passed_by is not None:  # a shadow book: only where the other arm chose to make no call on this very slot
            other = forecast.forecast_id(
                made.common.cycle_id, cfg.passed_by, made.common.asset_id, made.common.contract, made.common.horizon_ms
            )
            passed = histories.get(other)
            if passed is None or lifecycle.current_state(passed) is not lifecycle.State.NO_CALL:
                continue
        history = histories.get(record["id"])
        eligible = (
            None
            if history is None
            else next((t.step.eligible_after_ms for t in history if t.step.state is lifecycle.State.AWAITING_ORIGIN), None)
        )
        atr = next((a for ident, _ in made.common.evidence if (a := atr_of.get(ident)) is not None), None)
        if history is None or eligible is None or atr is None or atr <= 0:
            continue
        ended = lifecycle.current_state(history) in (lifecycle.State.VOID, lifecycle.State.UNSETTLEABLE, lifecycle.State.FAILED)
        out[record["id"]] = _Due(
            record["id"],
            record_digest(_clean(record)),
            made.common.asset_id,
            made.common.symbol,
            eligible,
            origin(eligible),
            made.common.horizon_ms,
            atr,
            ended,
        )
    return out


@dataclass
class _Ledgerbook:
    events: list[book.Event]
    buys: dict[str, book.Buy]  # by forecast id
    sold: set[str]


def books_of(records: Sequence[Mapping[str, Any]]) -> dict[str, _Ledgerbook]:
    """Each book's practice records in ledger order: its events, the purchases by forecast and the forecasts already sold."""
    found: dict[str, _Ledgerbook] = {}
    for record in records:
        if record.get("type") != practice_record.TYPE:
            continue
        entry = practice_record.from_record(_clean(record))
        held = found.setdefault(entry.name, _Ledgerbook([], {}, set()))
        held.events.append(entry.event)
        if entry.forecast_id is not None and isinstance(entry.event, book.Buy):
            held.buys[entry.forecast_id] = entry.event
        elif entry.forecast_id is not None:
            held.sold.add(entry.forecast_id)
    return found


@dataclass(frozen=True)
class Demand:
    """The bars the plan needs for one traded token: from the bar its purchases are filled in to the end of the last horizon."""

    asset_id: str
    symbol: str
    start_ms: int
    end_ms: int


def demands(records: Sequence[Mapping[str, Any]], cfg: Config) -> tuple[Demand, ...]:
    """What the plan will need to read now: the LONG forecasts that are not finished in every book (all sold, or ended with no purchase)."""
    ledgered = books_of(records)
    names = [practice_record.book_id(cfg.arm, level) for level in cfg.levels]
    out = []
    for fid, due in _longs(records, cfg).items():
        states = [ledgered.get(name) for name in names]
        sold = all(held is not None and fid in held.sold for held in states)
        unbought = due.ended and all(held is None or fid not in held.buys for held in states)
        if not sold and not unbought:
            out.append(
                Demand(due.asset_id, due.symbol, due.fill_open_ms_with(cfg.costs.latency_ms), due.t0_ms + due.horizon_ms + fills.MINUTE_MS)
            )
    return tuple(out)


def plan(
    records: Sequence[Mapping[str, Any]],
    cfg: Config,
    bars: Mapping[str, Mapping[int, Bar]],
    now_ms: int,
    source: str,
    clusters: Sequence[Sequence[str]] = (),
) -> Plan:
    """The practice records to append now, given everything in the ledger and the bars of the oracle. The arithmetic runs in a context of
    its own (sixty digits, half to even), so the plan is the same whatever context the caller has."""
    with localcontext(_CTX):
        return _plan(records, cfg, bars, now_ms, source, clusters)


def _plan(
    records: Sequence[Mapping[str, Any]],
    cfg: Config,
    bars: Mapping[str, Mapping[int, Bar]],
    now_ms: int,
    source: str,
    clusters: Sequence[Sequence[str]] = (),
) -> Plan:
    ledgered = books_of(records)
    longs = _longs(records, cfg)
    out: list[dict[str, canonical.JsonValue]] = []
    waiting: list[str] = []
    missed: list[str] = []
    refused: list[str] = []
    for level in cfg.levels:
        name = practice_record.book_id(cfg.arm, level)
        held = ledgered.get(name)
        if held is None:
            opening = book.Open(cfg.arm, level, cfg.equity, now_ms)
            current = book.apply(None, opening, cfg.limits)
            out.append(practice_record.to_record(name, opening, None, source, now_ms))
            held = _Ledgerbook([opening], {}, set())
        else:
            current = book.replay(held.events, cfg.limits)
        # the sales that are due come first: what is held is sold before more is bought
        for fid, bought_event in held.buys.items():
            due = longs.get(fid)
            if fid in held.sold:
                continue
            if due is None:  # a purchase whose forecast is no longer in the ledger's lifecycle: it cannot be sold by its rules
                missed.append(f"{name}/{fid}/exit")
                continue
            bought = fills.Fill(
                level,
                Status.FILLED,
                due.fill_open_ms_with(cfg.costs.latency_ms),
                bought_event.price,
                bought_event.quantity,
                bought_event.fee,
            )
            end = due.t0_ms + due.horizon_ms
            if current.mode in (Mode.FLAT, Mode.HALTED):  # the ladder has flattened the book: sold at the first bar after the last mark
                end = min(end, max(e.at_ms for e in held.events if isinstance(e, book.Mark)))
            stop, target = bought_event.price - K_STOP * due.atr, bought_event.price + K_TARGET * due.atr
            sale = fills.exit_order(
                level, bought, stop, target, end, bars.get(due.symbol, {}), cfg.costs, retries_exhausted=now_ms >= end + RETRY_MS
            )
            if sale.status is not Status.FILLED:
                (missed if sale.status is Status.MISSING else waiting).append(f"{name}/{fid}/exit")
                continue
            price, fee = cast(tuple[Decimal, Decimal], (sale.price, sale.fee))  # a filled sale has both
            sell = book.Sell(due.symbol, bought_event.quantity, price, fee, now_ms)
            current = book.apply(current, sell, cfg.limits)
            how = practice_record.Details(
                cast(Kind, sale.kind).value, cast(int, sale.bar_open_ms), sale.signal_open_ms, cfg.costs.latency_ms, bought.bar_open_ms
            )  # a filled sale has its kind and its bar
            out.append(practice_record.to_record(name, sell, (fid, due.digest), source, now_ms, how))
        # the purchases that are due
        ready: list[_Due] = []
        for fid, due in longs.items():
            if fid in held.buys or due.ended:
                continue
            opened = due.fill_open_ms_with(cfg.costs.latency_ms)
            if now_ms < opened + fills.MINUTE_MS:
                waiting.append(f"{name}/{fid}/entry")
                continue
            check = fills.entry(
                level,
                Decimal(1),
                due.eligible_after_ms,
                due.t0_ms,
                bars.get(due.symbol, {}),
                cfg.costs,
                retries_exhausted=now_ms >= opened + RETRY_MS,
            )
            if check.status is Status.FILLED:
                ready.append(due)
            else:
                (missed if check.status is Status.MISSING else waiting).append(f"{name}/{fid}/entry")
        for due, notional in _sized(ready, current, cfg, clusters):
            filled = fills.entry(level, notional, due.eligible_after_ms, due.t0_ms, bars.get(due.symbol, {}), cfg.costs)
            quantity, cost, fee = cast(
                tuple[Decimal, Decimal, Decimal], (filled.quantity, filled.price, filled.fee)
            )  # filled: the probe was
            buy = book.Buy(due.symbol, quantity, cost, fee, now_ms)  # the book refuses a purchase of nothing, which a tiny amount rounds to
            try:
                current = book.apply(current, buy, cfg.limits)
            except book.BookError:
                refused.append(f"{name}/{due.forecast_id}")
                continue
            how = practice_record.Details(None, cast(int, filled.bar_open_ms), None, cfg.costs.latency_ms, None)
            out.append(practice_record.to_record(name, buy, (due.forecast_id, due.digest), source, now_ms, how))
        prices = {}
        for position in current.positions:
            newest = max(bars.get(position.symbol, {}), default=None)
            if newest is not None:
                prices[position.symbol] = bars[position.symbol][newest].close
        stale = not current.positions and current.equity != current.cash  # flat, but the equity is still the last mark's
        if (current.positions or stale) and set(prices) == {p.symbol for p in current.positions}:
            mark = book.Mark(tuple(prices.items()), now_ms)  # the positions are by symbol, so the prices are
            out.append(practice_record.to_record(name, mark, None, source, now_ms))
    return Plan(tuple(out), tuple(waiting), tuple(missed), tuple(refused))


def _sized(ready: list[_Due], current: book.Book, cfg: Config, clusters: Sequence[Sequence[str]] = ()) -> list[tuple[_Due, Decimal]]:
    """The purchases of a pass: each request's notional, after the kernel has cut it to the room that is left.

    What is held is never sold to make room: the room under each cap is what the held weights leave, and the kernel (which knows the ladder
    and the rounding) cuts the request to that room. A request that finds no room buys nothing.

    A token in a cluster of tokens that move together (``clusters``) also finds the room under the cluster cap: what the cluster's members
    already hold counts against it."""
    equity = current.equity
    held = {p.symbol: (p.cost / equity).quantize(_SIX, rounding=ROUND_DOWN) for p in current.positions}
    out = []
    # every request has the one configured weight, so the order of contract 4.1 (weight, then symbol) is the symbol; the sort is stable
    for due in sorted(ready, key=lambda d: d.symbol):
        total, mine = sum(held.values(), Decimal(0)), held.get(due.symbol, Decimal(0))
        group = next((g for g in clusters if due.symbol in g), ())
        taken = sum((held.get(member, Decimal(0)) for member in group), Decimal(0))
        room = (
            cfg.limits.per_token - mine,
            cfg.limits.gross - total,
            cfg.limits.net - total,
            cfg.limits.cluster - taken if group else Decimal(1),
        )
        if min(room) <= 0:
            continue
        limits = replace(cfg.limits, per_token=room[0], gross=room[1], net=room[2], cluster=room[3] if group else cfg.limits.cluster)
        kernel = risk_kernel.shrink(
            {due.symbol: cfg.request_weight}, limits, drawdown=current.drawdown, halted=current.halted, clusters=(group,) if group else ()
        )
        share = kernel.weights[due.symbol]
        if share > 0:
            held[due.symbol] = mine + share
            out.append((due, (share * equity).quantize(_EIGHT, rounding=ROUND_DOWN)))
    return out
