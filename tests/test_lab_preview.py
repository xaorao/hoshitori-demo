"""The recorded preview of a hypothesis on real closed hourly candles (``scripts/make_lab_preview.py``): the fixture is what it says it is, and the
preview's figures recompute from the stored candles by an independent RSI, so they can be checked without the network."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import lab, lab_forward
from hoshitori.venue_data import Candle

HOUR = 3_600_000
PATH = Path(__file__).parent / "fixtures" / "lab_preview" / "btc_hourly.json"


def record() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PATH.read_text())
    return data


def candles(rec: dict[str, Any]) -> list[Candle]:
    out = []
    for row in rec["candles"]:
        opened = row["open_ms"]
        out.append(
            Candle(
                datetime.fromtimestamp(opened / 1000, UTC),
                datetime.fromtimestamp((opened + HOUR) / 1000, UTC),
                row["o"],
                row["h"],
                row["l"],
                row["c"],
                row["v"],
            )
        )
    return out


def wilder_rsi(closes: list[Decimal], period: int) -> list[Decimal | None]:
    """The RSI of Wilder: the first value from the plain mean of the first ``period`` changes, then the smoothed means; none before it."""
    out: list[Decimal | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    changes = [b - a for a, b in itertools.pairwise(closes)]
    gain = sum((max(c, Decimal(0)) for c in changes[:period]), Decimal(0)) / period
    loss = sum((max(-c, Decimal(0)) for c in changes[:period]), Decimal(0)) / period
    for i in range(period, len(closes)):
        if i > period:
            c = changes[i - 1]
            gain = (gain * (period - 1) + max(c, Decimal(0))) / period
            loss = (loss * (period - 1) + max(-c, Decimal(0))) / period
        out[i] = Decimal(100) if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def test_the_fixture_is_real_closed_hourly_candles_of_one_instrument() -> None:
    rec = record()
    assert (rec["venue"], rec["instrument"]) == ("binance-spot", "BTCUSDT")
    assert "not a forecast and not evidence" in rec["what"]
    rows = rec["candles"]
    assert len(rows) == 300
    assert all(b["open_ms"] - a["open_ms"] == HOUR for a, b in itertools.pairwise(rows))  # consecutive, once each
    assert rows[-1]["open_ms"] + HOUR <= rec["server_time_ms"]  # every candle had closed when the venue was asked
    for row in rows:
        o, h, low, c = (Decimal(row[k]) for k in ("o", "h", "l", "c"))
        assert low <= min(o, c) and h >= max(o, c) and low > 0
        assert Decimal(row["v"]) > 0


def test_the_preview_recomputes_from_the_stored_candles_with_an_independent_rsi(tmp_path: Path) -> None:
    rec = record()
    book = lab.Lab(tmp_path / "lab.jsonl")
    version = book.freeze(lab.draft_from(rec["proposal"]), "check", at_ms=rec["server_time_ms"])
    bars = lab_forward.bars_from(lab.grammar(), candles(rec))
    preview = book.preview(version, bars)
    stored = rec["preview"]
    assert (preview.label, preview.bars, preview.true_bars, preview.false_bars, preview.null_bars) == (
        stored["label"],
        stored["bars"],
        stored["true_bars"],
        stored["false_bars"],
        stored["null_bars"],
    )
    assert (preview.last_true_index, preview.first_ts, preview.last_ts) == (
        stored["last_true_index"],
        stored["first_ts"],
        stored["last_ts"],
    )
    rsi = wilder_rsi([Decimal(r["c"]) for r in rec["candles"]], 14)
    assert sum(v is None for v in rsi) == preview.null_bars
    assert sum(v is not None and v < 35 for v in rsi) == preview.true_bars
    assert sum(v is not None and v >= 35 for v in rsi) == preview.false_bars
    assert max(i for i, v in enumerate(rsi) if v is not None and v < 35) == preview.last_true_index
    monitor = book.monitor(version, bars)
    assert (monitor.state, monitor.reason) == (rec["monitor"]["state"], rec["monitor"]["reason"])
    assert preview.label.startswith("demonstration, not evidence")


def test_a_preview_is_not_a_forward_result(tmp_path: Path) -> None:
    """The same candles run forward from a freezing time at their end give no trade: nothing after the freezing has closed."""
    rec = record()
    book = lab.Lab(tmp_path / "lab.jsonl")
    version = book.freeze(lab.draft_from(rec["proposal"]), "check", at_ms=rec["server_time_ms"])
    spec = book.forward_spec(version)
    assert lab_forward.run(spec, candles(rec), {}, now_ms=rec["server_time_ms"]) == ()
