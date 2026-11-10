#!/usr/bin/env python3
"""Check an on-demand settlement without using the product's own readers or adapters.

    python scripts/ondemand_independent_check.py DIR

``DIR`` holds the on-demand ledger (``DIR/ondemand``). The script reads the forecast and the settlement record from the ledger's segment
file as plain JSON, fetches the same one-minute candles straight from the two public exchange APIs with the standard library, and
(1) recomputes the outcome by hand from the contract's rule, (2) builds a settlement case from those candles and asks both settlement
implementations (the Python one and the independently written Node one) for their answer, and (3) compares everything with the record.
It prints one line per comparison and exits 1 if any differs. Public, unauthenticated routes only; it makes no RYO and no model call.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

MINUTE = 60_000


def get(url: str) -> object:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed public https routes
        return json.loads(response.read())


def records(directory: Path) -> list[dict]:  # type: ignore[type-arg]
    found = []
    for path in sorted((directory / "ondemand" / "segments").glob("segment-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                found.append(json.loads(json.loads(line)["rec"]))
    return found


def binance_bars(symbol: str, first_ms: int, last_ms: int) -> dict[int, list[str]]:
    query = urllib.parse.urlencode({"symbol": symbol, "interval": "1m", "startTime": first_ms, "endTime": last_ms, "limit": 1000})
    rows = get(f"https://api.binance.com/api/v3/klines?{query}")
    return {int(r[0]): [r[1], r[2], r[3], r[4]] for r in rows}  # type: ignore[attr-defined]


def binance_tick(symbol: str) -> str:
    info = get(f"https://api.binance.com/api/v3/exchangeInfo?symbol={symbol}")
    for f in info["symbols"][0]["filters"]:  # type: ignore[index]
        if f["filterType"] == "PRICE_FILTER":
            return str(f["tickSize"]).rstrip("0").rstrip(".") if "." in str(f["tickSize"]) else str(f["tickSize"])
    raise SystemExit("no price filter")


def kraken_bars(pair: str, first_ms: int) -> tuple[dict[int, list[str]], int]:
    body = get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1&since={first_ms // 1000 - 60}")
    rows = next(v for k, v in body["result"].items() if k != "last")  # type: ignore[index]
    return {int(r[0]) * 1000: [r[1], r[2], r[3], r[4]] for r in rows}, int(body["result"]["last"])  # type: ignore[index]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write(__doc__ or "")
        return 2
    all_records = records(Path(argv[0]))
    forecast = next(r for r in all_records if r["type"] == "forecast")
    recorded = next(r for r in all_records if r["type"] == "settlement")
    fb, sb = forecast["body"], recorded["body"]
    attested = next(r["body"] for r in all_records if r["type"] == "forecast_state" and r["body"]["state"] == "AWAITING_ORIGIN")
    eligible = _ms(attested["eligible_after"])
    origin = (eligible // MINUTE + 1) * MINUTE  # the first one-minute candle that closes strictly after eligible_after, by its close
    horizon = int(fb["horizon_ms"])
    symbol = fb["symbol"] + "USDT"
    first, last = origin - 2 * MINUTE, origin + horizon + 2 * MINUTE
    primary = binance_bars(symbol, first, last)
    second, _ = kraken_bars(symbol, first)
    ref_open, out_open = origin - MINUTE, origin + horizon - MINUTE
    p0, ph = Decimal(primary[ref_open][3]), Decimal(primary[out_open][3])
    s0, sh = Decimal(second[ref_open][3]), Decimal(second[out_open][3])
    expected = "1" if ph > p0 else ("0" if ph < p0 else "TIE")
    bad: list[str] = []

    def line(label: str, ours: object, theirs: object) -> None:
        ok = ours == theirs
        sys.stdout.write(f"{'OK  ' if ok else 'FAIL'} {label}: record {ours!r}, independent {theirs!r}\n")
        if not ok:
            bad.append(label)

    line("state", sb["state"], "SETTLED")
    line("reference price P0 (Binance close of the candle closing at the origin)", sb["p0"], str(p0))
    line("outcome price (Binance close of the candle closing at the origin plus the horizon)", sb["outcome_price"], str(ph))
    line("outcome", sb["outcome"], expected)
    sys.stdout.write(f"     second oracle (Kraken): P0 {s0}, outcome {sh}; basis {abs(s0 - p0) / p0:.5f} and {abs(sh - ph) / ph:.5f}\n")
    line("the second oracle agrees within 0.5 percent at both ends", max(abs(s0 - p0) / p0, abs(sh - ph) / ph) <= Decimal("0.005"), True)
    case = {
        "id": "ondemand-live",
        "kind": "directional",
        "basis": None,
        "retries_exhausted": False,
        "forecast": {"forecast_id": sb["forecast_id"], "origin_ms": origin, "horizon_ms": horizon, "quote_class": fb["quote_class"]},
        "pair": {
            "pair_id": "A",
            "primary": _oracle("binance-spot", symbol, binance_tick(symbol), primary, server_ms=last + 10 * MINUTE),
            "second": _oracle("kraken", symbol, binance_tick(symbol), dict(second), server_ms=last + 10 * MINUTE),
        },
        "expected": {},
    }
    import tests.settlement_golden as golden

    from hoshitori import settlement

    ours = golden.run_case(settlement, case)
    node = json.loads(
        subprocess.run(  # noqa: S603
            ["node", str(REPO / "settlement-js" / "bin" / "answer.mjs"), "-"],  # noqa: S607
            input=json.dumps(case) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.splitlines()[0]
    )["answer"]
    line("Python settlement answer equals the Node one on these candles", ours, node)
    line("and both equal the record", (node.get("state"), node.get("outcome"), node.get("p0")), (sb["state"], sb["outcome"], sb["p0"]))
    return 1 if bad else 0


def _oracle(venue: str, instrument: str, tick: str, bars: dict[int, list[str]], *, server_ms: int) -> dict:  # type: ignore[type-arg]
    return {
        "venue": venue,
        "instrument": instrument,
        "quote_class": "USDT",
        "multiplier": "1",
        "tick": tick,
        "server_time_ms": server_ms,
        "bars": [[k, *v] for k, v in sorted(bars.items())],
    }


def _ms(text: str) -> int:
    from datetime import UTC, datetime

    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp() * 1000)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
