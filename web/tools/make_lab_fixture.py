"""A Laboratory log and a file of stored runs for the page tests of the Laboratory page. TEST DATA ONLY: the runs are made from a made-up price
series so that every state of a trade (closed, open, pending) appears; they are never part of the product's own site. The real candles
come from ``tests/fixtures/lab_preview/btc_hourly.json`` and are passed to the exporter beside the log.

usage: make_lab_fixture.py OUTDIR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hoshitori import lab

HOUR = 3_600_000
MINUTE = 60_000
H0 = 472_222 * HOUR
LONG = [105.0, 105.0, 105.0, 95.0, 95.0, 95.0, 95.0, 95.0, 101.0, 105.0, 105.0, 105.0, 105.0]


def proposal(symbol: str, direction: str, statement: str, **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "statement": statement,
        "symbol": symbol,
        "direction": direction,
        "horizon_h": 4,
        "condition": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 100}},
        "invalidation": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 90}},
        "max_weight": "0.02",
        "assumptions": [
            {"id": "a1", "kind": "OBSERVATION", "text": f"{symbol} trades below 100 from time to time"},
            {"id": "a2", "kind": "CAUSAL", "text": "a price below 100 is followed by a move back within four hours"},
        ],
    }
    body.update(over)
    return body


def hourly(closes: list[float]) -> list[dict[str, object]]:
    return [{"open_ms": H0 + i * HOUR, "o": str(p), "h": str(p), "l": str(p), "c": str(p), "v": "10"} for i, p in enumerate(closes)]


def minutes(closes: list[float]) -> list[list[object]]:
    out: list[list[object]] = []
    at = H0
    while at <= H0 + len(closes) * HOUR:
        p = str(closes[min((at - H0) // HOUR, len(closes) - 1)])
        out.append([at, p, p, p, p, "5"])
        at += MINUTE
    return out


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    log = out / "lab.jsonl"
    log.unlink(missing_ok=True)
    book = lab.Lab(log)
    real = json.loads(Path("tests/fixtures/lab_preview/btc_hourly.json").read_text())
    rsi = {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 35}}
    a = book.freeze(lab.draft_from(real["proposal"]), "author", at_ms=real["server_time_ms"])
    b = book.freeze(
        lab.draft_from(proposal("SOL", "UP", "SOL recovers within four hours of a close below 100")), "author", at_ms=H0 + 3 * HOUR
    )
    c = book.freeze(
        lab.draft_from(proposal("ETH", "DOWN", "ETH keeps falling within four hours of a close below 100", horizon_h=24)),
        "author",
        at_ms=H0 + 3 * HOUR,
    )
    d = book.freeze(lab.draft_from(proposal("DOGE", "UP", "DOGE bounces after an oversold reading", condition=rsi)), "author", at_ms=None)
    runs = {
        b: {"hourly": hourly(LONG), "minutes": minutes(LONG), "now_ms": H0 + len(LONG) * HOUR, "level": "BASE"},
        c: {"hourly": hourly(LONG), "minutes": minutes(LONG), "now_ms": H0 + 4 * HOUR + 2 * MINUTE, "level": "STRESS"},
    }
    (out / "runs.json").write_text(json.dumps(runs))
    print(json.dumps({"versions": [a, b, c, d]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
