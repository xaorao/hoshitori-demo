"""Practice for a frozen version of the Hypothesis Laboratory: what its rule would have done, trade by trade, from the moment it was frozen.

A frozen version (``lab.Lab.forward_spec``) is a rule arm: an entry condition, an invalidation condition, a direction, a horizon and a
largest weight, in the one condition grammar of the indicator skill. This module replays closed hourly candles through that rule and the
practice league's fill model. It is a pure function of its inputs: it reads no clock, no file and no network, and runs nothing but the
grammar's own evaluator.

* **Only the future counts.** A decision is made at the close of an hourly candle, and counts only if that close is after the moment the
  version was frozen. What the condition would have said earlier is a preview (``lab.Lab.preview``), never a result. A version with no
  freezing time cannot be run.
* **An entry** is taken at a decision when the entry condition is TRUE and the invalidation condition is not, and no trade is open. A
  condition that cannot be decided (too few bars for an indicator) is null, and null is never a signal, never an invalidation and never
  a reason to enter; it is not turned into false either, which is why the entry waits and the invalidation does not fire.
* **A trade** is bought through ``fills.entry`` (the authorisation is the close of the deciding candle; the fill is the open of the first
  one-minute bar after the latency) and sold at the first of two exits: the first decision whose invalidation condition is TRUE (the sale
  is the first minute after the latency), or the horizon, counted from the entry minute. A new trade can start at the first decision at or
  after the sale.
* **Three cost levels** are those of ``fills`` (base, stress, double fees). The size of a trade is the largest weight of the version times
  a fixed practice equity; it is not compounded, so trades do not depend on one another.
* **A long** is a purchase and a sale of the fill model. **A short** (direction DOWN) is a synthetic return, labelled so: the fill model
  is long-only, so the return is the negative of the move between the two raw opens, less the slippage and fee of both sides at the level.
  The stress level's adverse-extreme price is not used for a short.
* **Nothing is a result before it is closed.** A trade whose entry minute has not closed yet is ``PENDING``; one that is bought and not sold
  is ``OPEN`` and has no return; one whose minute never came is ``MISSING`` (or ``EXIT_MISSING`` for the sale), after the retries are over.
  A pending entry or an open or lost sale ends the run, because nothing after it is known.
* **What this is not.** A practice record of one rule on one token. It is not a forecast score, it is not committed or time-stamped, and
  with few trades it says nothing about skill (``summarize`` says so in words).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Final, cast

from hoshitori import fills, lab
from hoshitori.settlement import Bar
from hoshitori.venue_data import Candle

HOUR_MS: Final = 3_600_000
MINUTE_MS: Final = 60_000
EQUITY: Final = Decimal(100_000)  # the practice equity every trade is sized from; fixed, so trades are independent
_EIGHT: Final = Decimal("1E-8")
_BPS: Final = Decimal(10_000)
FEW: Final = 30  # below this many closed trades the summary says that nothing can be said
MANY: Final = 100


class ForwardError(ValueError):
    """A version or an input that cannot be run forward."""


@dataclass(frozen=True)
class Trade:
    version: str
    direction: str
    synthetic: bool  # a short is a synthetic return, not a fill
    level: str
    signal_at_ms: int  # the close of the candle on which the entry condition held
    status: str  # PENDING, MISSING, OPEN, EXIT_MISSING or CLOSED
    amount: Decimal | None  # what the trade put at risk: for a long, the cost of the purchase with its fee; for a short, the size
    entry_open_ms: int | None
    entry_price: Decimal | None
    exit_reason: str | None  # INVALIDATED or HORIZON; none while the trade is open and its horizon has not been reached
    exit_signal_at_ms: int | None  # the close of the candle on which the invalidation held; none for the horizon
    exit_open_ms: int | None
    exit_price: Decimal | None
    net_return: Decimal | None  # on the amount, after the costs of both sides; none until the trade is closed


@dataclass(frozen=True)
class Summary:
    closed: int
    open: int
    pending: int
    missing: int
    won: int
    lost: int
    mean_net_return: Decimal | None
    note: str


@dataclass(frozen=True)
class _Held:
    signal_at_ms: int
    entry_open_ms: int
    fill: fills.Fill


def _whole(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ForwardError(f"{what} is whole milliseconds, zero or more")
    return value


def _ms(moment: Any) -> int:
    seconds: float = moment.timestamp()
    return round(seconds * 1000)


def _checked(spec: Mapping[str, Any]) -> tuple[int, int, Decimal]:
    if spec.get("kind") != "RULE_ARM":
        raise ForwardError("only a rule arm is run forward")
    frozen = spec.get("frozen_at_ms")
    if frozen is None:
        raise ForwardError("this version has no freezing time, so it cannot be run forward")
    return _whole(frozen, "the freezing time"), int(spec["horizon_h"]) * HOUR_MS, Decimal(spec["max_weight"])


def bars_from(grammar: Any, candles: Sequence[Candle]) -> Any:
    """The grammar's bars for the candles, which are checked to be one hour each, consecutive and in order."""
    ts: list[int] = []
    volume: list[float] = []
    for c in candles:
        opened = _ms(c.open_time)
        if _ms(c.close_time) - opened != HOUR_MS:
            raise ForwardError("an hourly candle spans one hour")
        if ts and opened <= ts[-1]:
            raise ForwardError("the hourly candles are in ascending order, once each")
        if ts and opened != ts[-1] + HOUR_MS:
            raise ForwardError("the hourly candles have a gap")
        if c.volume is None:
            raise ForwardError("an hourly candle has no volume, which the grammar's bars need")
        ts.append(opened)
        volume.append(float(c.volume))
    return grammar.Bars(
        ts,
        [float(c.open) for c in candles],
        [float(c.high) for c in candles],
        [float(c.low) for c in candles],
        [float(c.close) for c in candles],
        volume,
    )


def _series(grammar: Any, condition: Mapping[str, Any], bars: Any) -> list[bool | None]:
    return list(grammar.evaluate(grammar.parse_condition(dict(condition)), bars).series)


def _round(value: Decimal) -> Decimal:
    """To eight places, half-even; called under the sixty-digit context of the caller."""
    return value.quantize(_EIGHT, rounding=ROUND_HALF_EVEN)


def _spent(fill: fills.Fill) -> Decimal:
    """The cost of a purchase: the quantity at the price, and the fee."""
    with localcontext() as ctx:
        ctx.prec = 60
        return _round(cast(Decimal, fill.quantity) * cast(Decimal, fill.price) + cast(Decimal, fill.fee))


def _net_long(held: _Held, sold: fills.Exit) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        return _round(cast(Decimal, sold.proceeds) / _spent(held.fill) - 1)


def _net_short(entry_open: Decimal, exit_open: Decimal, costs: fills.Costs, level: str) -> Decimal:
    fee_bps, slip_bps, _ = costs.at(level)
    with localcontext() as ctx:
        ctx.prec = 60
        return _round(-(exit_open / entry_open - 1) - 2 * (slip_bps + fee_bps) / _BPS)


def run(
    spec: Mapping[str, Any],
    hourly: Sequence[Candle],
    minutes: Mapping[int, Bar],
    *,
    now_ms: int,
    costs: fills.Costs | None = None,
    level: str = "BASE",
    retries_exhausted: bool = False,
) -> tuple[Trade, ...]:
    """The trades of a frozen version on closed hourly candles, with the one-minute bars the fills are taken from, as of ``now_ms``.

    Candles that have not closed by ``now_ms`` and minutes that have not closed by it are not read. ``retries_exhausted`` says that a
    minute that is not there will not come."""
    frozen, horizon_ms, weight = _checked(spec)
    _whole(now_ms, "the time")
    if level not in fills.LEVELS:
        raise ForwardError(f"a cost level is one of {fills.LEVELS}")
    costs = costs or fills.Costs()
    grammar = lab.grammar()
    bars = bars_from(grammar, hourly)
    seen = {k: b for k, b in minutes.items() if k + MINUTE_MS <= now_ms}
    count = sum(1 for t in bars.ts if t + HOUR_MS <= now_ms)  # the candles that have closed
    entry = _series(grammar, spec["entry"], bars)
    invalid = _series(grammar, spec["invalidation"], bars)
    short = spec["direction"] != "UP"
    notional = EQUITY * weight
    trades: list[Trade] = []

    def trade(signal: int, status: str, held: _Held | None, reason: str | None, exit_signal: int | None, sold: fills.Exit | None) -> Trade:
        if held is None:  # an entry that is awaited or that never came
            return Trade(
                str(spec["version"]), str(spec["direction"]), short, level, signal, status, None, None, None, None, None, None, None, None
            )
        bought = seen[held.entry_open_ms].open if short else cast(Decimal, held.fill.price)
        amount = notional if short else _spent(held.fill)
        ended = sold if sold is not None and sold.status is fills.Status.FILLED else None
        sale_open = None if ended is None else cast(int, ended.bar_open_ms)
        sale_price = None if ended is None else (seen[cast(int, sale_open)].open if short else ended.price)
        net = None
        if ended is not None:
            net = _net_short(bought, cast(Decimal, sale_price), costs, level) if short else _net_long(held, ended)
        return Trade(
            str(spec["version"]),
            str(spec["direction"]),
            short,
            level,
            signal,
            status,
            amount,
            held.entry_open_ms,
            bought,
            reason,
            exit_signal,
            sale_open,
            sale_price,
            net,
        )

    def sell(held: _Held, reason: str, exit_signal: int | None, end_ms: int) -> tuple[Trade, int | None]:
        """The trade after its sale was tried, and the minute the sale opened in; none when the sale is awaited or lost (the run ends)."""
        sold = fills.exit_at(level, held.fill, end_ms, seen, costs, retries_exhausted=retries_exhausted)
        if sold.status is fills.Status.FILLED:
            return trade(held.signal_at_ms, "CLOSED", held, reason, exit_signal, sold), sold.bar_open_ms
        status = "EXIT_MISSING" if sold.status is fills.Status.MISSING else "OPEN"
        return trade(held.signal_at_ms, status, held, reason, exit_signal, None), None

    holding: _Held | None = None
    free_from = frozen + 1  # a trade starts at a decision after the freezing, and after that at the first one at or after the last sale
    i = 0
    while i < count:
        decision = bars.ts[i] + HOUR_MS
        if holding is None:
            if decision < free_from or entry[i] is not True or invalid[i] is True:
                i += 1
                continue
            bought = fills.entry(level, notional, decision, decision, seen, costs, retries_exhausted=retries_exhausted)
            if bought.status is fills.Status.PENDING:
                trades.append(trade(decision, "PENDING", None, None, None, None))
                return tuple(trades)
            if bought.status is fills.Status.MISSING:
                trades.append(trade(decision, "MISSING", None, None, None, None))
            else:
                holding = _Held(decision, cast(int, bought.bar_open_ms), bought)
            i += 1
            continue
        end_ms = holding.entry_open_ms + horizon_ms
        if decision >= end_ms:
            done, sale_open = sell(holding, "HORIZON", None, end_ms)
            trades.append(done)
            if sale_open is None:
                return tuple(trades)
            holding, free_from = None, sale_open
            continue  # the same decision may start the next trade
        if invalid[i] is True:
            done, sale_open = sell(holding, "INVALIDATED", decision, decision + costs.latency_ms)
            trades.append(done)
            if sale_open is None:
                return tuple(trades)
            holding, free_from = None, sale_open
        i += 1
    if holding is not None:
        end_ms = holding.entry_open_ms + horizon_ms
        if now_ms >= end_ms:
            trades.append(sell(holding, "HORIZON", None, end_ms)[0])
        else:
            trades.append(trade(holding.signal_at_ms, "OPEN", holding, None, None, None))
    return tuple(trades)


def summarize(trades: Sequence[Trade]) -> Summary:
    """What the trades add up to, with the size of the sample said in words; a summary never ranks and never scores a forecast."""
    returns = [cast(Decimal, t.net_return) for t in trades if t.status == "CLOSED"]  # a closed trade has its return
    mean = None
    if returns:
        with localcontext() as ctx:
            ctx.prec = 60
            mean = _round(sum(returns, Decimal(0)) / len(returns))
    n = len(returns)
    if n == 0:
        note = "No closed trade yet."
    elif n < FEW:
        note = (
            f"{n} closed trade{'' if n == 1 else 's'}: too few to say anything about skill; "
            "this is a practice record and not a forecast score"
        )
    elif n < MANY:
        note = f"{n} closed trades: still a small sample; this is a practice record and not a forecast score"
    else:
        note = f"{n} closed trades; this is a practice record and not a forecast score"
    return Summary(
        n,
        sum(1 for t in trades if t.status == "OPEN"),
        sum(1 for t in trades if t.status == "PENDING"),
        sum(1 for t in trades if t.status in ("MISSING", "EXIT_MISSING")),
        sum(1 for r in returns if r > 0),
        sum(1 for r in returns if r < 0),
        mean,
        note,
    )
