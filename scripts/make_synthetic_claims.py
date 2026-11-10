#!/usr/bin/env python3
"""Write ``claims.json`` for the Claims page from invented claims and invented readings: no model is called and no market is read.

Each claim below was written for this file, and so was the reading of it (the nodes, the quoted words, the implied step). They stand in for
what a model would return, so that a fresh export has something on the page to look at. Every entry is labelled as an example whose model is
"synthetic example (no model)". They are not data about any market and say nothing about any token; the page says so.

The readings go through the real code: ``claim_extract.read_claim`` reads each one three times, ``claim_stability`` compares them, the plain
segmenter reads the text, and ``claims_file.record`` writes the entry. Only the model is replaced, by an answer that is written here.

usage: python scripts/make_synthetic_claims.py OUT.json
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from tests.model_client_checks import Script, answer

from hoshitori import claim_extract, claims_file
from hoshitori.model_client import ModelClient

MODEL = "synthetic example (no model)"
ASSETS = ("BTC", "ETH", "SOL")


def node(ident: str, kind: str, quote: str) -> dict[str, str]:
    return {"id": ident, "type": kind, "quote": quote}


EXAMPLES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "wallets-and-a-rally",
        "Large wallets are buying ETH, so a rally is starting, and ETH will gain 8% within a day.",
        {
            "nodes": [
                node("buy", "OBSERVATION", "Large wallets are buying ETH"),
                node("rally", "INTERPRETATION", "a rally is starting"),
                node("so", "CAUSAL_LINK", "so"),
                node("gain", "FORECAST", "ETH will gain 8% within a day"),
            ],
            "links": [{"start": "rally", "end": "buy", "kind": "DEPENDS_ON"}, {"start": "gain", "end": "rally", "kind": "DEPENDS_ON"}],
            "implied": [{"id": "who", "text": "The buying is by wallets that move the price", "needed_by": ["rally"]}],
        },
    ),
    (
        "a-listing-and-a-breakout",
        "If the exchange lists SOL this week, SOL will break out, because liquidity improves.",
        {
            "nodes": [
                node("list", "CONDITION", "If the exchange lists SOL this week"),
                node("out", "FORECAST", "SOL will break out"),
                node("why", "CAUSAL_LINK", "because liquidity improves"),
            ],
            "links": [{"start": "out", "end": "list", "kind": "CONDITIONAL_ON"}],
            "implied": [],
        },
    ),
    (
        "a-move-and-its-reason",
        "BTC fell 3% in the last hour because of the rate decision.",
        {
            "nodes": [
                node("fell", "OBSERVATION", "BTC fell 3% in the last hour"),
                node("because", "CAUSAL_LINK", "because of the rate decision"),
            ],
            "links": [{"start": "because", "end": "fell", "kind": "CAUSAL"}],
            "implied": [],
        },
    ),
    (
        "an-opinion",
        "SOL is the best chain and the market is too bearish on it.",
        {
            "nodes": [node("best", "OPINION", "SOL is the best chain"), node("bear", "OPINION", "the market is too bearish on it")],
            "links": [],
            "implied": [],
        },
    ),
)


def read_all() -> list[dict[str, Any]]:
    out = []
    config = claim_extract.Config(MODEL, max_tokens=2500, timeout_s=120)
    for ident, text, reading in EXAMPLES:
        runs = [answer(json.dumps(reading), n) for n in range(1, claim_extract.RUNS + 1)]
        client = ModelClient(Script(*runs))
        found = claim_extract.read_claim(client, text, config, ASSETS)
        at = datetime(2026, 1, 1, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # a fixed time: the file is the same every time it is made
        out.append(claims_file.record(ident, text, found, MODEL, at, ASSETS))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__ or "")
        return 2
    records = read_all()
    data = (json.dumps(records, indent=1, ensure_ascii=False) + "\n").encode()
    claims_file.read_claims(data)  # the page's own reader accepts what is written
    with open(sys.argv[1], "wb") as handle:
        handle.write(data)
    sys.stdout.write(f"wrote {sys.argv[1]}: {len(records)} claims, none read by a model\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
