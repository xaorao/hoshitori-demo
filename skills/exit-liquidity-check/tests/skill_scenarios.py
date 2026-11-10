"""Hand-built scenarios for the sweep of ``skill``: each is a request and the replies the two sources give."""

from __future__ import annotations

from typing import Any

from exit_liquidity_check.registry import TOKENS
from tests.support import FakeTransport

ETH = TOKENS["ETH"]["address"]
BONK = TOKENS["BONK"]["address"]


def pool(symbol: str = "BONK", pair: str = "P1", liq: float = 1_000_000, vol: float = 500_000, price: str = "0.00002") -> dict[str, Any]:
    return {
        "pairAddress": pair,
        "dexId": "dex",
        "baseToken": {"address": TOKENS[symbol]["address"]},
        "quoteToken": {"symbol": "USDC"},
        "liquidity": {"usd": liq},
        "volume": {"h24": vol},
        "priceUsd": price,
    }


REF = {"inAmount": "20000000", "outAmount": "100000000"}
SIZED = {"inAmount": "1000000000", "outAmount": "4950000000", "priceImpactPct": "0.004", "routePlan": [1, 2]}
FREE = {"inAmount": "1000000000", "outAmount": "5100000000"}  # better than the reference rate: a cost of zero


def eth_pool(pair: str, liq: float, vol: float) -> dict[str, Any]:
    return pool("ETH", pair, liq, vol, "3000")


# name -> (arguments, dexscreener replies, jupiter replies)
SCENARIOS: dict[str, tuple[dict[str, Any], list[Any] | None, list[Any] | None]] = {
    "quoted_ok": ({"token": "BONK", "size_usd": 5000}, [[pool()]], [REF, SIZED]),
    "quoted_tight": ({"token": "BONK", "size_usd": 5000, "max_slippage_pct": 0.8}, [[pool()]], [REF, SIZED]),
    "quoted_illiquid": ({"token": "BONK", "size_usd": 5000, "max_slippage_pct": 0.4}, [[pool()]], [REF, SIZED]),
    "quoted_free": ({"token": "BONK", "size_usd": 5000}, [[pool()]], [REF, FREE]),
    "quoted_odd": (
        {"token": "BONK", "size_usd": 7777.77, "max_slippage_pct": 1.23456789, "participation_pct": 7.654321},
        [[pool(liq=1_234_567.891, vol=543_210.987)]],
        [REF, {"inAmount": "1555554000", "outAmount": "7700123457", "priceImpactPct": "0.00123456"}],
    ),
    "modelled_illiquid": (
        {"token": "ETH", "size_usd": 1_000_000, "max_slippage_pct": 2, "participation_pct": 5},
        [[eth_pool("A", 20_000_000, 40_000_000), eth_pool("B", 10_000_000, 10_000_000)]],
        None,
    ),
    "modelled_tight": ({"token": "ETH", "size_usd": 10_000, "max_slippage_pct": 1.5}, [[eth_pool("A", 1_000_000, 5_000_000)]], None),
    "modelled_ok": ({"token": "ETH", "size_usd": 10_000}, [[eth_pool("A", 1_000_000, 5_000_000)]], None),
    "outside_model": ({"token": "ETH", "size_usd": 400_000}, [[eth_pool("A", 1_000_000, 10_000_000)]], None),
    "no_size_solana": ({"token": "BONK"}, [[pool()]], None),
    "no_size_ethereum": ({"token": "ETH"}, [[eth_pool("A", 1_000_000, 5_000_000)]], None),
    "depth_dead": ({"token": "BONK", "size_usd": 50_000}, [503], None),
    "depth_junk": ({"token": "BONK", "size_usd": 1}, [b"<html>"], None),
    "depth_empty": ({"token": "BONK", "size_usd": 1}, [[]], None),
    "depth_no_size_dead": ({"token": "BONK"}, [503], None),
    "quote_dead": ({"token": "BONK", "size_usd": 5000}, [[pool()]], [400]),
    "quiet_pool": ({"token": "ETH", "size_usd": 1000}, [[eth_pool("A", 1_000_000, 19_999)]], None),
    "edge_quiet_pool": ({"token": "ETH", "size_usd": 1000}, [[eth_pool("A", 1_000_000, 20_000)]], None),
}


def transport_for(name: str) -> FakeTransport:
    _, dex, jup = SCENARIOS[name]
    return FakeTransport(dex=dex, jup=jup)
