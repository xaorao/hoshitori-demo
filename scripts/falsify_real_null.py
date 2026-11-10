#!/usr/bin/env python3
"""Run the Desk's two falsification tests on real exchange minutes with no planted event, as a check on the planted-world calibration.

The calibration (``falsify_calibration``) measures the tests on synthetic series. This runs them on real one-minute candles of six tokens
(Binance, public market data) against Bitcoin as the market proxy, in windows laid out like a Desk run (``desk_history.PRE_MINUTES`` before
the announcement, then the horizon), one window an hour, and counts the labels. No announcement happened at those times, but real news and
real market moves did, so a share of ``FAVOURS_ALTERNATIVE`` is a measure of how often the tests would call a real window something else; a
rate far above the synthetic null of the calibration would mean the calibration is optimistic. Nothing is written and nothing is sent but
public candle requests.

usage: python scripts/falsify_real_null.py [DAYS]
"""

from __future__ import annotations

import itertools
import json
import sys
import time
import urllib.request

from hoshitori import desk_history, falsify

HOST = "https://data-api.binance.vision"
TOKENS = ("ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT")


def closes(symbol: str, minutes: int) -> dict[int, float]:
    """The closes of the last ``minutes`` finished one-minute bars, by the minute each opened."""
    end = int(time.time() // 60 * 60 * 1000) - 60_000
    out: dict[int, float] = {}
    at = end - minutes * 60_000
    while at < end:
        url = f"{HOST}/api/v3/klines?symbol={symbol}&interval=1m&startTime={at}&limit=1000"
        request = urllib.request.Request(url, headers={"User-Agent": "hoshitori-audit"})  # noqa: S310 - a fixed https host
        with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310
            rows = json.loads(reply.read())
        if not rows:
            break
        out.update({int(row[0]): float(row[4]) for row in rows})
        at = int(rows[-1][0]) + 60_000
        time.sleep(0.15)
    return out


def main(days: int) -> int:
    minutes = days * 24 * 60
    market = closes("BTCUSDT", minutes)
    counts: dict[tuple[int, str], dict[str, int]] = {}
    pre = desk_history.PRE_MINUTES
    for symbol in TOKENS:
        token = closes(symbol, minutes)
        times = sorted(set(token) & set(market))
        returns: list[tuple[float, float]] = [
            (token[b] / token[a] - 1, market[b] / market[a] - 1) for a, b in itertools.pairwise(times) if b - a == 60_000
        ]
        for hours in (1, 4):
            horizon = 60 * hours
            length = pre + horizon + 10
            for start in range(0, len(returns) - length, 60):
                window = returns[start : start + length]
                own, proxy = [x for x, _ in window], [y for _, y in window]
                for name, result in (
                    ("T_ONSET", falsify.t_onset(own, pre, horizon)),
                    ("T_MARKET", falsify.t_market(own, proxy, pre, horizon)),
                ):
                    row = counts.setdefault((hours, name), {})
                    row[result.result] = row.get(result.result, 0) + 1
    for (hours, name), row in sorted(counts.items()):
        total = sum(row.values())
        shares = ", ".join(f"{label} {n / total:.3f}" for label, n in sorted(row.items()))
        sys.stdout.write(f"{hours} h {name}: {total} windows: {shares}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
