#!/usr/bin/env python3
"""Choose the wallets the Voices and whales page follows, by a rule fixed before anyone looks at how they performed.

The cohort is the largest accounts by account value on the snapshot, plus a random sample of the other accounts that are worth at least a
floor, drawn with a seed that is written down. Nothing about past profit or loss is read (contract section 21: a leaderboard of winners
builds survivorship in before any model runs). A vault, a market maker and a directional trader look alike here and no role is inferred.

Usage: python3 scripts/make_whale_cohort.py OUT.json [--top 20] [--sample 20] [--floor 1000000] [--seed 1] [--from FILE]
OUT.json is a signals settings file (accounts and feeds empty) with the rule and the snapshot's size recorded under "cohort_rule", which
``hoshitori-league signals`` ignores. The snapshot is downloaded once through the safe fetch client unless ``--from`` names a saved file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoshitori import safe_fetch, whales

URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def choose(rows: list[dict[str, object]], top: int, sample: int, floor: Decimal, seed: int) -> list[str]:
    """The addresses: the ``top`` by account value, then ``sample`` drawn with ``seed`` from the rest at or above ``floor``."""
    valued: list[tuple[Decimal, str]] = []
    for row in rows:
        address, value = row.get("ethAddress"), row.get("accountValue")
        if not isinstance(address, str) or not whales.valid_wallet(address):
            continue
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            continue
        if amount.is_finite():
            valued.append((amount, address.lower()))
    valued.sort(key=lambda p: (-p[0], p[1]))
    chosen = [a for _, a in valued[:top]]
    pool = sorted(a for v, a in valued[top:] if v >= floor)
    chosen += random.Random(seed).sample(pool, min(sample, len(pool)))  # noqa: S311  (a written-down seed, not security)
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--floor", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--from", dest="source", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.top + args.sample > 50 or args.top < 0 or args.sample < 0:
        print("the cohort is at most 50 wallets (the page reads each once)", file=sys.stderr)
        return 2
    raw = (
        args.source.read_bytes()
        if args.source
        else safe_fetch.SafeFetcher(limits=safe_fetch.FetchLimits(max_body_bytes=64 * 1024 * 1024, timeout_s=120.0)).fetch(URL).body
    )
    rows = json.loads(raw)["leaderboardRows"]
    wallets = choose(rows, args.top, args.sample, Decimal(args.floor), args.seed)
    rule = {
        "source": URL,
        "snapshot_rows": len(rows),
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "chosen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "top_by_account_value": args.top,
        "random_sample": args.sample,
        "sample_floor_usd": args.floor,
        "seed": args.seed,
        "uses_performance": False,
    }
    args.out.write_text(
        json.dumps({"accounts": [], "wallets": wallets, "symbols": ["BTC", "ETH", "SOL"], "cohort_rule": rule}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"wallets": len(wallets), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
