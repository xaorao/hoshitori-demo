"""Record a preview of one hypothesis on real closed hourly candles (``tests/fixtures/lab_preview/``).

One run asks Binance spot for the closed hourly candles of one instrument through the venue adapter's own request and reader, freezes the
hypothesis in a throw-away Laboratory book, previews its entry condition on those candles and writes the candles, the hypothesis and the
preview to one JSON file. ``tests/test_lab_preview.py`` recomputes the preview from the stored candles, so the figures can be checked
without the network. Nothing here is committed or time-stamped: the preview is a demonstration on past data, and says so.

usage: make_lab_preview.py OUT.json [INSTRUMENT [HOURS]]
"""

from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import lab, lab_forward
from hoshitori.venue_base import VenueConfig
from hoshitori.venue_binance import BinanceSpot
from hoshitori.venue_data import Candle

HOST = "https://api.binance.com"
PROPOSAL = {
    "statement": "BTC bounces within four hours after the hourly RSI is below 35",
    "symbol": "BTC",
    "direction": "UP",
    "horizon_h": 4,
    "condition": {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 35}},
    "invalidation": {"left": {"indicator": "close"}, "op": "<", "right": {"indicator": "lowest_prior", "period": 24}},
    "max_weight": "0.02",
    "assumptions": [
        {"id": "a1", "kind": "OBSERVATION", "text": "the hourly RSI of BTC falls below 35 from time to time"},
        {"id": "a2", "kind": "CAUSAL", "text": "an oversold reading is followed by a bounce within four hours"},
    ],
}


def get(adapter: BinanceSpot, operation: str, args: dict[str, Any]) -> bytes:
    request = adapter.build_request(operation, args)
    with urllib.request.urlopen(HOST + request.path, timeout=30) as answer:  # noqa: S310 (a fixed https host)
        body: bytes = answer.read(adapter.max_answer_bytes + 1)
    if len(body) > adapter.max_answer_bytes:
        raise SystemExit("the answer is larger than the adapter reads")
    return body


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    instrument = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
    hours = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    adapter = BinanceSpot(VenueConfig())
    server = adapter.parse_time_body(get(adapter, "time", {}))
    end = server.replace(minute=0, second=0, microsecond=0)  # the open of the hour that is forming now: it is excluded
    start = end - timedelta(hours=hours)
    candles: list[Candle] = adapter.parse_candles_body(
        get(adapter, "candles", {"instrument": instrument, "interval_s": 3600, "start": start.isoformat(), "end": end.isoformat()}),
        {"interval_s": 3600},
    )
    closed = [c for c in candles if c.close_time <= server]
    with tempfile.TemporaryDirectory() as tmp:
        book = lab.Lab(Path(tmp) / "lab.jsonl")
        draft = lab.draft_from(PROPOSAL)
        version = book.freeze(draft, "recorded-preview", at_ms=int(server.timestamp() * 1000))
        grammar = lab.grammar()
        preview = book.preview(version, lab_forward.bars_from(grammar, closed))
        monitor = book.monitor(version, lab_forward.bars_from(grammar, closed))
    record = {
        "what": "a preview of one hypothesis on real closed hourly candles: a demonstration on past data, not a forecast and not evidence",
        "venue": adapter.venue,
        "instrument": instrument,
        "server_time_ms": int(server.timestamp() * 1000),
        "proposal": PROPOSAL,
        "candles": [
            {"open_ms": round(c.open_time.timestamp() * 1000), "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
            for c in closed
        ],
        "preview": {
            "label": preview.label,
            "bars": preview.bars,
            "true_bars": preview.true_bars,
            "false_bars": preview.false_bars,
            "null_bars": preview.null_bars,
            "last_true_index": preview.last_true_index,
            "first_ts": preview.first_ts,
            "last_ts": preview.last_ts,
        },
        "monitor": {"state": monitor.state, "reason": monitor.reason},
    }
    out.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps({"candles": len(closed), **record["preview"], "monitor": record["monitor"]}))  # type: ignore[dict-item]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
