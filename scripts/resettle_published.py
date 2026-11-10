#!/usr/bin/env python3
"""Re-settle every published up-or-down call from public exchange data, as an outsider would.

    python3 scripts/resettle_published.py DATA_DIR [--binance BASE_URL] [--limit N]

``DATA_DIR`` is the ``data`` folder of a published site (what ``hoshitori-league site`` writes, or ``docs/record/record.tar.gz`` unpacked).
The script uses nothing of the product: no module of it, only Python's standard library and Binance's public, unauthenticated candle
route. For every receipt of a settled up-or-down call it fetches the one-minute candles that close at the call's start and at its start
plus its window, and checks three things against the receipt: the start price is the close of the candle that closes at the start, the
end price is the close of the candle that closes at the end, and the outcome (rose if the end is above the start, fell if below) is the
one recorded. It prints one line per call and a count, and exits 1 if any call does not match.

What it does not check, said plainly: the second oracle (Kraken keeps only its most recent 720 one-minute candles, so most published
calls are older than it can serve); target-or-stop calls (their levels are not in the published receipt); calls that could not be
settled. Those are counted as not checked, never as passed.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

MINUTE = 60_000
BINANCE = "https://api.binance.com"
PAGE = 1000  # Binance returns at most this many candles a request
UTC_ZONE = timezone.utc  # noqa: UP017 - an outsider's Python may be 3.9, which has no datetime.UTC


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC_ZONE).timestamp() * 1000)


def _get(url: str) -> object:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - a fixed public route, or the loopback a test names
        return json.loads(response.read())


class Candles:
    """Binance's one-minute closes, fetched once per symbol and range and kept by open time."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.closes: dict[str, dict[int, str]] = {}

    def close_at(self, symbol: str, open_ms: int) -> str | None:
        known = self.closes.setdefault(symbol, {})
        if open_ms not in known:  # one page from this minute on: the next calls of the same symbol usually fall inside it
            query = urllib.parse.urlencode({"symbol": symbol, "interval": "1m", "startTime": open_ms, "limit": PAGE})
            for row in _get(f"{self.base}/api/v3/klines?{query}"):  # type: ignore[attr-defined]
                known[int(row[0])] = str(row[4])
        return known.get(open_ms)


def main(argv: list[str]) -> int:
    args = list(argv)
    base, limit = BINANCE, None
    if "--binance" in args:
        i = args.index("--binance")
        base = args[i + 1]
        del args[i : i + 2]
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        del args[i : i + 2]
    if len(args) != 1:
        sys.stderr.write(__doc__ or "")
        return 2
    data = Path(args[0])
    receipts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((data / "receipts").glob("*.json"))]
    settled = [r for r in receipts if r["row"]["contract"] == "D" and r["settlement"] is not None and r["settlement"]["state"] == "SETTLED"]
    not_checked = len(receipts) - len(settled)
    settled.sort(key=lambda r: (r["row"]["symbol"], r["row"]["origin"]))
    if limit is not None:
        not_checked += max(len(settled) - limit, 0)
        settled = settled[:limit]
    candles = Candles(base)
    passed, failed, missing = 0, 0, 0
    for r in settled:
        row, s = r["row"], r["settlement"]
        symbol, start = row["symbol"] + "USDT", _ms(row["origin"])
        end = start + row["hours"] * 3_600_000
        p0 = candles.close_at(symbol, start - MINUTE)
        ph = candles.close_at(symbol, end - MINUTE)
        if p0 is None or ph is None:
            missing += 1
            sys.stdout.write(f"NOT CHECKED {row['forecast_id']} {row['symbol']} {row['hours']}h: Binance has no candle there\n")
            continue
        outcome = "Rose" if Decimal(ph) > Decimal(p0) else ("Fell" if Decimal(ph) < Decimal(p0) else "Tie")
        same = (
            Decimal(p0) == Decimal(s["reference_price"]),
            Decimal(ph) == Decimal(s["outcome_price"]),
            outcome == row["result"],
        )
        if all(same):
            passed += 1
            continue
        failed += 1
        sys.stdout.write(
            f"FAIL {row['forecast_id']} {row['symbol']} {row['hours']}h: start {s['reference_price']} against {p0}, "
            f"end {s['outcome_price']} against {ph}, outcome {row['result']} against {outcome}\n"
        )
    sys.stdout.write(
        f"re-settled from Binance: {passed} match, {failed} do not, {missing} without a candle; "
        f"{not_checked} not checked (not settled, target-or-stop, or past the limit)\n"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
