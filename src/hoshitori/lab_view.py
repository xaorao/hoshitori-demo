"""The Hypothesis Laboratory as a document for the interface (``lab.json``): each frozen version in words, what it assumes, a preview of its
entry condition on stored real candles, and what its rule has done since it was frozen.

Every figure is read from something that exists: the versions from the Laboratory's command log, the preview from the closed hourly candles
of a record the product stored (``scripts/make_lab_preview.py``, never fetched here), and the trades from ``lab_forward.run`` on candles and
one-minute bars of a record the product stored. A version with no stored candles has no preview and says why; one with no stored run says
that nothing was run, and never shows a trade that was not made. A preview is labelled a demonstration on past data. Nothing here reads a
clock, a file or the network: the caller gives it the records.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from hoshitori import lab, lab_forward
from hoshitori.settlement import Bar
from hoshitori.venue_data import Candle

FORMAT: Final = 1
HOUR_MS: Final = 3_600_000
NO_CANDLES: Final = "no real candles of this token are stored with this site, so its condition has not been previewed"
NO_RUN: Final = "no run of this version on candles after it was frozen is stored with this site, so it has made no trade to show"
_OPS: Final = {
    "<": "is below",
    "<=": "is at or below",
    ">": "is above",
    ">=": "is at or above",
    "==": "equals",
    "cross_above": "crosses above",
    "cross_below": "crosses below",
}
_PRICES: Final = {"open": "the open", "high": "the high", "low": "the low", "close": "the close", "volume": "the volume"}


class LabViewError(ValueError):
    """A record that cannot be shown."""


@dataclass(frozen=True)
class AssumptionView:
    id: str
    kind: str
    text: str


@dataclass(frozen=True)
class PreviewView:
    label: str
    venue: str
    instrument: str
    bars: int
    true_bars: int
    false_bars: int
    undecided_bars: int
    last_true_at_ms: int | None
    first_at_ms: int
    last_at_ms: int
    invalidation: str  # INVALIDATED, HOLDS or UNKNOWN on the last candle
    invalidation_reason: str | None


@dataclass(frozen=True)
class TradeView:
    status: str
    direction: str
    synthetic: bool
    signal_at_ms: int
    entry_at_ms: int | None
    entry_price: str | None
    exit_reason: str | None
    exit_signal_at_ms: int | None
    exit_at_ms: int | None
    exit_price: str | None
    amount: str | None
    net_return: str | None


@dataclass(frozen=True)
class ForwardView:
    run: bool
    reason: str | None  # why nothing was run
    level: str | None
    as_of_ms: int | None
    trades: tuple[TradeView, ...]
    closed: int
    open: int
    pending: int
    missing: int
    won: int
    lost: int
    mean_net_return: str | None
    note: str


@dataclass(frozen=True)
class EntryView:
    version: str
    statement: str
    symbol: str
    direction: str
    horizon_h: int
    max_weight: str
    frozen_at_ms: int | None
    by: str
    parent: str | None
    enters_when: str
    invalid_when: str
    assumptions: tuple[AssumptionView, ...]
    preview: PreviewView | None
    preview_reason: str | None
    forward: ForwardView


@dataclass(frozen=True)
class Document:
    format: int
    versions: tuple[EntryView, ...]


# --- stored records -------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CandleRecord:
    """Closed hourly candles of one instrument as the product stored them."""

    venue: str
    instrument: str
    symbol: str  # the token the instrument is about (BTC for BTCUSDT): the version's ``symbol``
    candles: tuple[Candle, ...]


@dataclass(frozen=True)
class RunRecord:
    """What a run needs: the hourly candles after (and before) the freezing, the one-minute bars the fills come from, and the clock."""

    hourly: tuple[Candle, ...]
    minutes: Mapping[int, Bar]
    now_ms: int
    level: str
    retries_exhausted: bool


def _int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LabViewError(f"{what} is whole milliseconds, zero or more")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise LabViewError(f"{what} is a non-empty text")
    return value


def _decimal(value: object, what: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        raise LabViewError(f"{what} is a decimal number") from None
    if not out.is_finite():
        raise LabViewError(f"{what} is a decimal number")
    return out


def _candle(row: object) -> Candle:
    if not isinstance(row, dict) or set(row) != {"open_ms", "o", "h", "l", "c", "v"}:
        raise LabViewError("a candle has open_ms, o, h, l, c and v")
    opened = _int(row["open_ms"], "a candle's open time")
    prices = [_decimal(row[k], f"a candle's {k}") for k in ("o", "h", "l", "c")]
    volume = _decimal(row["v"], "a candle's volume")
    o, h, low, c = prices
    if low <= 0 or not low <= min(o, c) or not h >= max(o, c) or volume < 0:
        raise LabViewError("a candle's range holds its open and close, its prices are positive and its volume is zero or more")
    return Candle(
        datetime.fromtimestamp(opened / 1000, UTC),
        datetime.fromtimestamp((opened + HOUR_MS) / 1000, UTC),
        str(row["o"]),
        str(row["h"]),
        str(row["l"]),
        str(row["c"]),
        str(row["v"]),
    )


def read_candles(data: bytes) -> CandleRecord:
    """A stored candle record (``scripts/make_lab_preview.py``), read strictly."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabViewError(f"the candle record is not JSON: {exc}") from exc
    if not isinstance(doc, dict) or not {"venue", "instrument", "proposal", "candles"} <= set(doc):
        raise LabViewError("a candle record has a venue, an instrument, a proposal and candles")
    symbol = doc["proposal"].get("symbol") if isinstance(doc["proposal"], dict) else None
    rows = doc["candles"]
    if not isinstance(rows, list) or not rows:
        raise LabViewError("a candle record has at least one candle")
    candles = tuple(_candle(row) for row in rows)
    starts = [round(c.open_time.timestamp() * 1000) for c in candles]
    if any(b - a != HOUR_MS for a, b in itertools.pairwise(starts)):
        raise LabViewError("the candles are consecutive hours, once each")
    return CandleRecord(_text(doc["venue"], "the venue"), _text(doc["instrument"], "the instrument"), _text(symbol, "the token"), candles)


def read_runs(data: bytes) -> dict[str, RunRecord]:
    """The stored runs, by version: ``{"version": {"hourly": [candles], "minutes": [[open_ms, o, h, l, c, v]], "now_ms", "level"}}``."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabViewError(f"the runs file is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise LabViewError("the runs file is an object of versions")
    out: dict[str, RunRecord] = {}
    for version, body in doc.items():
        if (
            not isinstance(body, dict)
            or not {"hourly", "minutes", "now_ms", "level"} <= set(body)
            or not set(body)
            <= {
                "hourly",
                "minutes",
                "now_ms",
                "level",
                "retries_exhausted",
            }
        ):
            raise LabViewError(f"{version}: a run has hourly, minutes, now_ms and level, and may say retries_exhausted")
        minutes: dict[int, Bar] = {}
        for row in body["minutes"]:
            if not isinstance(row, list) or len(row) != 6:
                raise LabViewError(f"{version}: a minute is [open_ms, o, h, l, c, v]")
            opened = _int(row[0], "a minute's open time")
            o, h, low, c = (_decimal(x, "a minute's price") for x in row[1:5])
            volume = _decimal(row[5], "a minute's volume")
            try:
                minutes[opened] = Bar(opened, o, h, low, c, volume)
            except ValueError as exc:
                raise LabViewError(f"{version}: {exc}") from exc
        if not isinstance(body["hourly"], list):
            raise LabViewError(f"{version}: the hourly candles are a list")
        exhausted = body.get("retries_exhausted", False)
        if not isinstance(exhausted, bool):
            raise LabViewError(f"{version}: retries_exhausted is true or false")
        out[version] = RunRecord(
            tuple(_candle(r) for r in body["hourly"]),
            minutes,
            _int(body["now_ms"], "the time"),
            _text(body["level"], "the level"),
            exhausted,
        )
    return out


# --- words ----------------------------------------------------------------------------------------------------------------------------
def _number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LabViewError("a constant is a number")
    return str(int(value)) if float(value).is_integer() else str(value)


def _operand(raw: object) -> str:
    if not isinstance(raw, dict):
        raise LabViewError("an operand is an object")
    if "value" in raw:
        return _number(raw["value"])
    name = raw.get("indicator")
    if name in _PRICES:
        return _PRICES[str(name)]
    p = raw
    if name == "sma":
        return f"the simple average of the {p['source']} over {p['period']} bars"
    if name == "ema":
        return f"the exponential average of the {p['source']} over {p['period']} bars"
    if name == "rsi":
        return f"the RSI of the {p['source']} over {p['period']} bars"
    if name == "atr":
        return f"the average true range over {p['period']} bars"
    if name == "atr_pct":
        return f"the average true range over {p['period']} bars as a share of the close"
    if name == "volume_zscore":
        return f"the volume's distance from its {p['period']}-bar average, in standard deviations"
    if name == "roc":
        return f"the change of the {p['source']} over {p['period']} bars"
    if name == "highest_prior":
        return f"the highest {p['source']} of the previous {p['period']} bars"
    if name == "lowest_prior":
        return f"the lowest {p['source']} of the previous {p['period']} bars"
    if name == "macd":
        return f"the MACD {p['output']} ({p['fast']}, {p['slow']}, {p['signal']})"
    if name == "bbands":
        return f"the Bollinger {p['output']} ({p['period']} bars, {_number(p['stddev'])} deviations)"
    if name == "stoch":
        return f"the stochastic {p['output']} ({p['k_period']}, {p['smooth']}, {p['d_period']})"
    raise LabViewError(f"an indicator is one the grammar names, not {name!r}")


def words(node: object, depth: int = 0) -> str:
    """A condition of the grammar, normalised, in plain words; a shape the grammar does not have is refused."""
    if not isinstance(node, dict):
        raise LabViewError("a condition is an object")
    if set(node) == {"left", "op", "right"}:
        if node["op"] not in _OPS:
            raise LabViewError(f"an operator is one of {list(_OPS)}")
        return f"{_operand(node['left'])} {_OPS[node['op']]} {_operand(node['right'])}"
    if len(node) == 1 and next(iter(node)) in ("all", "any"):
        kind, children = next(iter(node.items()))
        if not isinstance(children, list) or not children:
            raise LabViewError("a group has at least one condition")
        inner = "; ".join(words(c, depth + 1) for c in children)
        text = f"{'all' if kind == 'all' else 'any'} of: {inner}"
        return f"({text})" if depth else text
    if set(node) == {"not"}:
        return f"not ({words(node['not'], depth + 1)})"
    raise LabViewError("a condition is a clause, an all, an any or a not")


# --- the document ---------------------------------------------------------------------------------------------------------------------
def _trade(t: lab_forward.Trade) -> TradeView:
    return TradeView(
        t.status,
        t.direction,
        t.synthetic,
        t.signal_at_ms,
        t.entry_open_ms,
        None if t.entry_price is None else str(t.entry_price),
        t.exit_reason,
        t.exit_signal_at_ms,
        t.exit_open_ms,
        None if t.exit_price is None else str(t.exit_price),
        None if t.amount is None else str(t.amount),
        None if t.net_return is None else str(t.net_return),
    )


def _forward(spec: Mapping[str, Any], run: RunRecord | None) -> ForwardView:
    if spec["frozen_at_ms"] is None:
        return ForwardView(
            False,
            "this version was frozen before the moment was recorded, so it cannot be run forward",
            None,
            None,
            (),
            0,
            0,
            0,
            0,
            0,
            0,
            None,
            "No closed trade yet.",
        )
    if run is None:
        return ForwardView(False, NO_RUN, None, None, (), 0, 0, 0, 0, 0, 0, None, "No closed trade yet.")
    try:
        trades = lab_forward.run(spec, run.hourly, run.minutes, now_ms=run.now_ms, level=run.level, retries_exhausted=run.retries_exhausted)
    except lab_forward.ForwardError as exc:
        raise LabViewError(f"{spec['version']}: {exc}") from exc
    s = lab_forward.summarize(trades)
    return ForwardView(
        True,
        None,
        run.level,
        run.now_ms,
        tuple(_trade(t) for t in trades),
        s.closed,
        s.open,
        s.pending,
        s.missing,
        s.won,
        s.lost,
        None if s.mean_net_return is None else str(s.mean_net_return),
        s.note,
    )


def _preview(book: lab.Lab, version: str, record: CandleRecord) -> PreviewView:
    grammar = lab.grammar()
    bars = lab_forward.bars_from(grammar, record.candles)
    p = book.preview(version, bars)
    m = book.monitor(version, bars)
    last = None if p.last_true_index is None else bars.ts[p.last_true_index]
    return PreviewView(
        p.label,
        record.venue,
        record.instrument,
        p.bars,
        p.true_bars,
        p.false_bars,
        p.null_bars,
        last,
        p.first_ts,
        p.last_ts,
        m.state,
        m.reason,
    )


def build(book: lab.Lab, candles: Sequence[CandleRecord] = (), runs: Mapping[str, RunRecord] | None = None) -> Document:
    """The document of a Laboratory book. ``candles`` are stored real candle records, one per token; ``runs`` are stored runs by version."""
    by_symbol: dict[str, CandleRecord] = {}
    for record in candles:
        if record.symbol in by_symbol:
            raise LabViewError(f"the candles of {record.symbol} are given once")
        by_symbol[record.symbol] = record
    runs = runs or {}
    unknown = set(runs) - set(book.versions())
    if unknown:
        raise LabViewError(f"a run is stored for a version that is not in the book: {sorted(unknown)[0]}")
    entries: list[EntryView] = []
    for key, version in book.versions().items():
        m = version.mandate
        spec = book.forward_spec(key)
        stored = by_symbol.get(m.symbol)
        entries.append(
            EntryView(
                key,
                m.statement,
                m.symbol,
                m.direction,
                m.horizon_h,
                str(m.max_weight),
                version.frozen_at_ms,
                version.by,
                version.parent,
                words(m.condition),
                words(m.invalidation),
                tuple(AssumptionView(a.id, a.kind, a.text) for a in m.assumptions),
                None if stored is None else _preview(book, key, stored),
                NO_CANDLES if stored is None else None,
                _forward(spec, runs.get(key)),
            )
        )
    return Document(FORMAT, tuple(entries))
