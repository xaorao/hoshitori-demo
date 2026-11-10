"""Parsing the venue replies: recorded live replies and hand-built hostile ones."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from exit_liquidity_check import venues
from exit_liquidity_check.errors import SourceError
from exit_liquidity_check.registry import TOKENS

FIX = Path(__file__).parent / "fixtures"
BONK = TOKENS["BONK"]["address"]


def pool(**kw: Any) -> dict[str, Any]:
    base = {
        "pairAddress": "P1",
        "dexId": "raydium",
        "baseToken": {"address": BONK, "symbol": "Bonk"},
        "quoteToken": {"symbol": "SOL"},
        "liquidity": {"usd": 1_000_000},
        "volume": {"h24": 500_000},
        "priceUsd": "0.00002",
    }
    base.update(kw)
    return base


def test_recorded_dexscreener_reply() -> None:
    recorded = json.loads((FIX / "dexscreener_bonk.json").read_text())
    depth = venues.parse_pairs("BONK", recorded["payload"])
    assert depth.pools and depth.total_liquidity_usd and depth.total_liquidity_usd > 0
    assert depth.pools == tuple(sorted(depth.pools, key=lambda p: (-p.liquidity_usd, p.pair)))
    assert depth.total_liquidity_usd == pytest.approx(sum(p.liquidity_usd for p in depth.pools))
    assert depth.source == f"dexscreener:solana/{BONK}"
    assert len(depth.pools) + len(depth.rejected) == len(recorded["payload"])


def test_each_rejection_has_its_reason() -> None:
    payload = [
        pool(pairAddress="ok"),
        pool(pairAddress="quote-side", baseToken={"address": "Other"}),
        pool(pairAddress="no-liq", liquidity={"usd": 0}),
        pool(pairAddress="no-vol", volume={"h24": 0}),
        pool(pairAddress="no-price", priceUsd=None),
        pool(pairAddress="junk-quote", quoteToken={"symbol": "SCAMCOIN"}),
        pool(pairAddress="far-price", priceUsd="0.00004"),
        "not a pool",
    ]
    depth = venues.parse_pairs("BONK", payload)
    reasons = dict(depth.rejected)
    assert [p.pair for p in depth.pools] == ["ok"]
    assert "quote side" in reasons["quote-side"]
    assert "no reported liquidity" in reasons["no-liq"]
    assert "24 hours" in reasons["no-vol"]
    assert "no usable price" in reasons["no-price"]
    assert "SCAMCOIN" in reasons["junk-quote"]
    assert "3% from the median" in reasons["far-price"]


def test_impostor_with_huge_liquidity_adds_nothing() -> None:
    payload = [pool(pairAddress="real"), pool(pairAddress="impostor", liquidity={"usd": 277_000_000}, priceUsd="0.034")]
    depth = venues.parse_pairs("BONK", payload)
    assert depth.total_liquidity_usd == 1_000_000
    assert dict(depth.rejected)["impostor"].startswith("price")


def test_no_pool_passes() -> None:
    depth = venues.parse_pairs("BONK", [pool(liquidity={"usd": None})])
    assert depth.pools == () and depth.total_liquidity_usd is None and depth.price_usd is None


@pytest.mark.parametrize("payload", [None, {}, "x", 3])
def test_a_payload_that_is_not_a_list_is_a_bad_response(payload: Any) -> None:
    with pytest.raises(SourceError) as err:
        venues.parse_pairs("BONK", payload)
    assert err.value.reason == "bad_response"


def test_hostile_numbers_are_not_numbers() -> None:
    for bad in (True, "NaN", "inf", [1], {"a": 1}):
        depth = venues.parse_pairs("BONK", [pool(liquidity={"usd": bad})])
        assert depth.pools == ()


def test_weighted_median() -> None:
    assert venues.weighted_median([(1.0, 1.0), (2.0, 1.0), (3.0, 10.0)]) == 3.0
    assert venues.weighted_median([(1.0, 0.0), (5.0, 0.0), (3.0, 0.0)]) == 3.0


def test_recorded_jupiter_quote() -> None:
    rec = json.loads((FIX / "jupiter_bonk.json").read_text())
    quote = venues.parse_quote("BONK", rec["reference"], rec["sized"], size_usd=50_000)
    assert 0.0 < quote.effective_cost < 0.2
    assert quote.out_usd == pytest.approx(int(rec["sized"]["outAmount"]) / 1e6)
    assert quote.route_hops == len(rec["sized"]["routePlan"])
    assert quote.as_dict()["basis"] == "quoted"


def test_quote_cost_hand_computed() -> None:
    ref = {"inAmount": "1000", "outAmount": "2000"}  # near-mid rate 2.0
    sized = {"inAmount": "100000", "outAmount": "190000", "priceImpactPct": "0.04", "routePlan": [{}, {}]}
    quote = venues.parse_quote("BONK", ref, sized, size_usd=1.0)
    assert quote.effective_cost == pytest.approx(1 - 190000 / 200000)
    assert quote.venue_price_impact == 0.04 and quote.route_hops == 2
    better = venues.parse_quote("BONK", ref, {"inAmount": "100000", "outAmount": "210000"}, size_usd=1.0)
    assert better.effective_cost == 0.0  # a quote above the near-mid rate is not a negative cost


@pytest.mark.parametrize(
    "ref,sized",
    [
        ({"inAmount": "0", "outAmount": "1"}, {"inAmount": "1", "outAmount": "1"}),
        ({"inAmount": "1", "outAmount": "0"}, {"inAmount": "1", "outAmount": "1"}),
        ({"inAmount": "1", "outAmount": "1"}, {"inAmount": "1", "outAmount": "0"}),
        ({"inAmount": "1", "outAmount": "1"}, {"error": "no route"}),
        ({"inAmount": "x", "outAmount": "1"}, {"inAmount": "1", "outAmount": "1"}),
        ({"inAmount": True, "outAmount": "1"}, {"inAmount": "1", "outAmount": "1"}),
        ([], {}),
    ],
)
def test_unusable_quotes_are_bad_responses(ref: Any, sized: Any) -> None:
    with pytest.raises(SourceError) as err:
        venues.parse_quote("BONK", ref, sized, size_usd=1.0)
    assert err.value.reason == "bad_response"
