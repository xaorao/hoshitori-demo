#!/usr/bin/env python3
"""Which scan cutoff a monthly RYO allowance pays for.

    python scripts/ryo_budget.py ALLOWANCE [--event-cap 5] [--watchlist 5] [--readings-a-day 1] [--days 30]

A cycle that reads RYO makes 3 + 2n calls for n candidates (``hoshitori.ryo_budget``). The candidates of a cycle are at most the scan
cutoff, the event cap and the watchlist together. The script prints the largest scan cutoff that fits, and what it costs a month; give it
to ``make-protocol`` as the protocol's scan cutoff. It sends nothing and reads no key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoshitori import ryo_budget


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("allowance", type=int, help="the calls a month the key allows")
    ap.add_argument("--event-cap", type=int, default=5)
    ap.add_argument("--watchlist", type=int, default=5, help="how many symbols the protocol's watchlist holds")
    ap.add_argument("--readings-a-day", type=int, default=1)
    ap.add_argument("--days", type=int, default=ryo_budget.DAYS_A_MONTH)
    args = ap.parse_args(argv)
    try:
        scan = ryo_budget.largest_scan_cutoff(args.allowance, args.event_cap, args.watchlist, args.readings_a_day, args.days)
    except ryo_budget.BudgetError as exc:
        sys.stderr.write(f"ryo_budget: {exc}\n")
        return 2
    candidates = scan + args.event_cap + args.watchlist
    print(
        json.dumps(
            {
                "scan_cutoff": scan,
                "candidates_at_most": candidates,
                "calls_per_reading": ryo_budget.calls_per_reading(candidates),
                "calls_a_month": ryo_budget.calls_a_month(candidates, args.readings_a_day, args.days),
                "allowance": args.allowance,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
