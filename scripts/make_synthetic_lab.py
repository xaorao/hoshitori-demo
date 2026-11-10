#!/usr/bin/env python3
"""Write a Laboratory command log of invented hypotheses, for a fresh export: no market is read and no candle is stored.

Each hypothesis below was written for this file. Every statement starts with "Invented example:" and every version is frozen by
"synthetic-example", so the Laboratory page shows what a frozen version looks like (the statement, its condition and invalidation, its
assumptions, its parent) and says in each version's own words that it is an example. There are no previews and no forward runs here: those
need real candles and the time to pass.

usage: python scripts/make_synthetic_lab.py OUT.jsonl
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from hoshitori import lab

BY = "synthetic-example"
AT_MS = 1_767_225_600_000  # a fixed moment, so that the file is the same every time it is made


def proposal(statement: str, symbol: str, limit: int, stop: int, weight: str, assumption: str) -> dict[str, Any]:
    return {
        "statement": statement,
        "symbol": symbol,
        "direction": "UP",
        "horizon_h": 4,
        "condition": {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": limit}},
        "invalidation": {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": stop}},
        "max_weight": weight,
        "assumptions": [{"id": "a1", "kind": "OBSERVATION", "text": assumption}],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write(__doc__ or "")
        return 2
    out = Path(argv[0])
    if out.exists():
        sys.stderr.write(f"{out} exists; remove it first\n")
        return 2
    book = lab.Lab(out)
    first = book.freeze(
        lab.draft_from(
            proposal(
                "Invented example: BTC bounces within four hours after the hourly RSI falls below 35",
                "BTC",
                35,
                25,
                "0.02",
                "invented: RSI dips are common",
            )
        ),
        BY,
        at_ms=AT_MS,
    )
    book.freeze(
        lab.draft_from(
            proposal(
                "Invented example: the same idea, with a stricter entry at 30 (a child of the first version)",
                "BTC",
                30,
                25,
                "0.01",
                "invented: a deeper dip bounces more",
            )
        ),
        BY,
        parent=first,
        at_ms=AT_MS + 3_600_000,
    )
    book.freeze(
        lab.draft_from(
            proposal(
                "Invented example: ETH bounces within four hours after the hourly RSI falls below 35",
                "ETH",
                35,
                25,
                "0.02",
                "invented: RSI dips are common",
            )
        ),
        BY,
        at_ms=AT_MS + 7_200_000,
    )
    sys.stdout.write(f"wrote {out}: {len(book.versions())} invented versions, no previews, no runs\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
