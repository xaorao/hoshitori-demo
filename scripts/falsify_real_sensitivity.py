#!/usr/bin/env python3
"""Sensitivity of the F-01 real-history result to how its uncertainty is measured (exploratory).

The frozen validation (``falsify_real_validation.py``) resamples market days one at a time. This asks whether the answer depends on that
choice: a moving-block bootstrap over two-, three- and five-day blocks of consecutive days (days that follow one another share a market
regime), the result with each seven-day week left out, and the rate of each token on its own. It is labelled exploratory because the
held-out period had already been read when it was written; nothing here changes the frozen result, the digest or the gate.

usage: python scripts/falsify_real_sensitivity.py CACHE
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import falsify_real_validation as v

BLOCKS = (1, 2, 3, 5)
DRAWS = 2000
SEED = 20260507


def _daily(windows: list[dict[str, object]], test: str) -> dict[int, list[int]]:
    days: dict[int, list[int]] = {}
    for w in windows:
        if w["dropped"]:
            continue
        hit, count = days.setdefault(int(str(w["day"])), [0, 0])
        days[int(str(w["day"]))][0] = hit + int(w["labels"][test] == "FAVOURS_ALTERNATIVE")  # type: ignore[index]
        days[int(str(w["day"]))][1] = count + 1
    return days


def _block_interval(days: dict[int, list[int]], length: int, rng: random.Random) -> tuple[float, float]:
    ordered = [days[d] for d in sorted(days)]
    n = len(ordered)
    starts = max(1, n - length + 1)
    rates = []
    for _ in range(DRAWS):
        k = c = picked = 0
        while picked < n:
            start = rng.randrange(starts)
            for hit, count in ordered[start : start + length]:
                k += hit
                c += count
            picked += length
        rates.append(k / c if c else math.nan)
    rates.sort()
    return rates[int(0.025 * DRAWS)], rates[int(0.975 * DRAWS)]


def _leave_out_week(days: dict[int, list[int]]) -> tuple[float, float]:
    ordered = sorted(days)
    weeks = [ordered[i : i + 7] for i in range(0, len(ordered), 7) if len(ordered[i : i + 7]) == 7]
    rates = []
    for gone in weeks:
        k = sum(days[d][0] for d in ordered if d not in gone)
        c = sum(days[d][1] for d in ordered if d not in gone)
        rates.append(k / c if c else math.nan)
    return (min(rates), max(rates)) if rates else (math.nan, math.nan)


def main(cache: Path) -> int:
    series, meta = v._load(cache)
    rng = random.Random(SEED)  # noqa: S311 - a bootstrap, not a secret
    sys.stdout.write("exploratory sensitivity; the frozen held-out result is not changed\n")
    for horizon in v.HORIZONS:
        windows = v._windows(series, meta, horizon)
        dev, held = v._split(series, meta, horizon)
        for test in v.TESTS:
            for name, part in (("all 90 days", windows), ("development", dev), ("held-out", held)):
                days = _daily(part, test)
                if not days:
                    continue
                k = sum(h for h, _ in days.values())
                n = sum(c for _, c in days.values())
                intervals = ", ".join(
                    f"{length}-day blocks [{lo:.3f}, {hi:.3f}]" for length in BLOCKS for lo, hi in [_block_interval(days, length, rng)]
                )
                low, high = _leave_out_week(days)
                sys.stdout.write(
                    f"{horizon} h {test} {name}: rate {k / n:.3f} (n {n}, days {len(days)}); {intervals}; "
                    f"leaving one week out: {low:.3f} to {high:.3f}\n"
                )
            per_token: dict[str, list[int]] = {}
            for w in windows:
                if not w["dropped"]:
                    row = per_token.setdefault(str(w["symbol"]), [0, 0])
                    row[0] += int(w["labels"][test] == "FAVOURS_ALTERNATIVE")  # type: ignore[index]
                    row[1] += 1
            shares = ", ".join(f"{t[:-4]} {h / n:.3f}" for t, (h, n) in sorted(per_token.items()))
            sys.stdout.write(f"{horizon} h {test} by token (all 90 days): {shares}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])) if len(sys.argv) == 2 else 2)
