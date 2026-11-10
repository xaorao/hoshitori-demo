#!/usr/bin/env python3
"""Held-out real-history validation of the falsification permission (finding F-01).

It runs the Desk's two falsification tests on real one-minute candles in windows with no announcement, counts how often each calls
``FAVOURS_ALTERNATIVE`` (the real-null alternative rate) and puts an interval on it. This is a proxy for the nuisance side only: real
windows hold real news, and no real window has a known true effect, so it cannot measure the calibrated quantity (true effects wrongly cut).

The method is frozen mechanically. ``prepare`` downloads the last ``--days`` days into a cache folder and prints the data digest.
``develop``
prints the development period (the older two thirds) and the method digest (the sha256 of this file and the data digest). ``held-out``
refuses to run unless it is given that exact method digest, so the held-out period cannot be read before the method is frozen, and a
change to this file after the freeze changes the digest and blocks the read.

Scored windows do not overlap (the step is the window length), and uncertainty comes from a bootstrap over market days.

usage: python scripts/falsify_real_validation.py prepare CACHE [--days 90]
       python scripts/falsify_real_validation.py develop CACHE
       python scripts/falsify_real_validation.py held-out CACHE METHOD_DIGEST
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from hoshitori import desk_history, falsify

HOST = "https://data-api.binance.vision"
MARKET = "BTCUSDT"
TOKENS = ("ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT")
HORIZONS = (1, 4)
TESTS = ("T_ONSET", "T_MARKET")
DEVELOPMENT_SHARE = 2 / 3
BOOTSTRAP_DRAWS = 2000
SEED = 20260506
LINE = 0.10  # falsify.MAX_FALSE_ALTERNATIVE, restated here only to apply the decision rule
DAY_MS = 86_400_000
MINUTE_MS = 60_000


def _fetch(symbol: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Closes by open time for the minutes in ``[start_ms, end_ms)``; a failed page is retried three times and then raises."""
    out: dict[int, float] = {}
    at = start_ms
    while at < end_ms:
        url = f"{HOST}/api/v3/klines?symbol={symbol}&interval=1m&startTime={at}&limit=1000"
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "hoshitori-audit"})  # noqa: S310 - a fixed https host
                with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310
                    rows = json.loads(reply.read())
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        if not rows:
            break
        out.update({int(row[0]): float(row[4]) for row in rows})
        at = int(rows[-1][0]) + MINUTE_MS
        time.sleep(0.12)
    return out


def prepare(cache: Path, days: int) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    end = int(time.time() // 60 * 60 * 1000) - MINUTE_MS
    end -= end % DAY_MS  # whole UTC days only
    start = end - days * DAY_MS
    for symbol in (MARKET, *TOKENS):
        closes = _fetch(symbol, start, end)
        (cache / f"{symbol}.json").write_text(json.dumps(sorted(closes.items())), encoding="utf-8")
        sys.stdout.write(f"{symbol}: {len(closes)} minutes of {days * 1440}\n")
    (cache / "meta.json").write_text(json.dumps({"start_ms": start, "end_ms": end, "days": days}), encoding="utf-8")
    sys.stdout.write(f"data digest {_data_digest(cache)}\n")


def _data_digest(cache: Path) -> str:
    h = hashlib.sha256()
    for name in ("meta.json", f"{MARKET}.json", *(f"{t}.json" for t in TOKENS)):
        h.update(name.encode())
        h.update((cache / name).read_bytes())
    return h.hexdigest()


def _method_digest(cache: Path) -> str:
    return hashlib.sha256(Path(__file__).read_bytes() + _data_digest(cache).encode()).hexdigest()


def _load(cache: Path) -> tuple[dict[str, dict[int, float]], dict[str, int]]:
    series = {s: {int(a): float(b) for a, b in json.loads((cache / f"{s}.json").read_text())} for s in (MARKET, *TOKENS)}
    return series, json.loads((cache / "meta.json").read_text())


def _windows(series: dict[str, dict[int, float]], meta: dict[str, int], horizon_hours: int) -> list[dict[str, object]]:
    """Non-overlapping windows per token, each with its announcement-time day, regime volatility and the two test labels."""
    pre = desk_history.PRE_MINUTES
    horizon = 60 * horizon_hours
    length = pre + horizon + 10
    out: list[dict[str, object]] = []
    market = series[MARKET]
    for symbol in TOKENS:
        token = series[symbol]
        start = meta["start_ms"]
        total = (meta["end_ms"] - start) // MINUTE_MS
        for first in range(0, total - length - 1, length):
            times = [start + (first + k) * MINUTE_MS for k in range(length + 1)]
            if any(t not in token or t not in market for t in times):
                out.append({"day": (times[pre] - start) // DAY_MS, "dropped": True, "vol": 0.0, "labels": {}, "symbol": symbol})
                continue
            own = [token[b] / token[a] - 1 for a, b in itertools.pairwise(times)]
            proxy = [market[b] / market[a] - 1 for a, b in itertools.pairwise(times)]
            pre_block = proxy[:pre]
            vol = statistics.pstdev(pre_block)
            labels = {
                "T_ONSET": falsify.t_onset(own, pre, horizon).result,
                "T_MARKET": falsify.t_market(own, proxy, pre, horizon).result,
            }
            out.append({"day": (times[pre] - start) // DAY_MS, "dropped": False, "vol": vol, "labels": labels, "symbol": symbol})
    return out


def _wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    z, p = 1.959964, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - w) / d, (c + w) / d)


def _cluster_interval(rows: list[tuple[int, int]], rng: random.Random) -> tuple[float, float]:
    """95 percent bootstrap interval of sum(k)/sum(n) with the market day as the resampled unit; rows are (day, hit) with n=1 each."""
    by_day: dict[int, list[int]] = {}
    for day, hit in rows:
        by_day.setdefault(day, [0, 0])
        by_day[day][0] += hit
        by_day[day][1] += 1
    days = list(by_day.values())
    rates = []
    for _ in range(BOOTSTRAP_DRAWS):
        k = n = 0
        for _day in range(len(days)):
            hit, count = days[rng.randrange(len(days))]
            k += hit
            n += count
        rates.append(k / n if n else float("nan"))
    rates.sort()
    return (rates[int(0.025 * BOOTSTRAP_DRAWS)], rates[int(0.975 * BOOTSTRAP_DRAWS)])


def _report(
    windows: list[dict[str, object]], label: str, thresholds: tuple[float, float] | None, rng: random.Random
) -> tuple[float, float]:
    kept = [w for w in windows if not w["dropped"]]
    dropped = len(windows) - len(kept)
    vols = sorted(float(w["vol"]) for w in kept)  # type: ignore[arg-type]
    if thresholds is None:
        thresholds = (vols[len(vols) // 3], vols[2 * len(vols) // 3])
    for test in TESTS:
        rows = [(int(w["day"]), int(w["labels"][test] == "FAVOURS_ALTERNATIVE")) for w in kept]  # type: ignore[index, call-overload]
        k, n = sum(h for _, h in rows), len(rows)
        low, high = _cluster_interval(rows, rng)
        wl, wh = _wilson(k, n)
        shares: dict[str, int] = {}
        for w in kept:
            shares[w["labels"][test]] = shares.get(w["labels"][test], 0) + 1  # type: ignore[index]
        verdict = "UPPER<=10%" if high <= LINE else ("LOWER>10%" if low > LINE else "STRADDLES 10%")
        sys.stdout.write(
            f"  {label} {test}: windows {n} (dropped {dropped}), days {len({d for d, _ in rows})}, rate {k / n:.3f}, "
            f"day-bootstrap 95% [{low:.3f}, {high:.3f}], window-Wilson [{wl:.3f}, {wh:.3f}], rule: {verdict}; "
            f"labels {dict(sorted(shares.items()))}\n"
        )
        for name, test_rows in _by_regime(kept, test, thresholds):
            kk, nn = sum(test_rows), len(test_rows)
            sys.stdout.write(f"      regime {name}: windows {nn}, rate {kk / nn:.3f}\n" if nn else f"      regime {name}: no windows\n")
    return thresholds


def _by_regime(kept: list[dict[str, object]], test: str, thresholds: tuple[float, float]) -> list[tuple[str, list[int]]]:
    low, high = thresholds
    groups: dict[str, list[int]] = {"low-vol": [], "mid-vol": [], "high-vol": []}
    for w in kept:
        v = float(w["vol"])  # type: ignore[arg-type]
        name = "low-vol" if v <= low else ("mid-vol" if v <= high else "high-vol")
        groups[name].append(int(w["labels"][test] == "FAVOURS_ALTERNATIVE"))  # type: ignore[index]
    return list(groups.items())


def _rolling(windows: list[dict[str, object]], span_days: int, test: str) -> str:
    kept = [w for w in windows if not w["dropped"]]
    days = sorted({int(w["day"]) for w in kept})  # type: ignore[call-overload]
    rates = []
    for first in range(days[0], days[-1] + 1 - span_days + 1, span_days):
        block = [w for w in kept if first <= int(w["day"]) < first + span_days]  # type: ignore[call-overload]
        if len(block) >= 10:
            rates.append(sum(w["labels"][test] == "FAVOURS_ALTERNATIVE" for w in block) / len(block))  # type: ignore[index]
    if not rates:
        return "no block"
    return f"{span_days}-day blocks: n={len(rates)}, min {min(rates):.3f}, median {statistics.median(rates):.3f}, max {max(rates):.3f}"


def _split(
    series: dict[str, dict[int, float]], meta: dict[str, int], horizon: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    windows = _windows(series, meta, horizon)
    cut = int(meta["days"] * DEVELOPMENT_SHARE)
    # a window belongs to the period its whole span lies in: the cut is applied to the announcement day and one window length of margin is
    # left out on the development side so no development window shares a minute with a held-out one
    dev = [w for w in windows if int(w["day"]) < cut - 1]  # type: ignore[call-overload]
    held = [w for w in windows if int(w["day"]) >= cut]  # type: ignore[call-overload]
    return dev, held


def run(cache: Path, held_out: bool) -> None:
    series, meta = _load(cache)
    sys.stdout.write(f"data digest {_data_digest(cache)}\nmethod digest {_method_digest(cache)}\n")
    rng = random.Random(SEED)  # noqa: S311 - a bootstrap, not a secret
    for horizon in HORIZONS:
        dev, held = _split(series, meta, horizon)
        sys.stdout.write(f"{horizon} h horizon\n")
        thresholds = _report(dev, "development", None, rng)
        for test in TESTS:
            for span in (2, 6, 20):
                sys.stdout.write(f"  development {test} {_rolling(dev, span, test)}\n")
        if held_out:
            _report(held, "HELD-OUT", thresholds, rng)
            for test in TESTS:
                for span in (2, 6):
                    sys.stdout.write(f"  held-out {test} {_rolling(held, span, test)}\n")


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "prepare":
        prepare(Path(argv[1]), int(argv[3]) if len(argv) > 3 and argv[2] == "--days" else 90)
        return 0
    if len(argv) == 2 and argv[0] == "develop":
        run(Path(argv[1]), held_out=False)
        return 0
    if len(argv) == 3 and argv[0] == "held-out":
        if argv[2] != _method_digest(Path(argv[1])):
            sys.stderr.write("the method digest does not match this file and this data: the method is not the frozen one\n")
            return 2
        run(Path(argv[1]), held_out=True)
        return 0
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
