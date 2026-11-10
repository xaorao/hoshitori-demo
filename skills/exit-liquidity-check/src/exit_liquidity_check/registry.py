"""Canonical contracts. A symbol is a name anyone can reuse; an address is the token.

Pools are only ever looked up by these addresses, never by symbol: searching a symbol on a pool index returns, among the deepest "BONK"
pools, other tokens that share the name (Hanko's own check found $277m of reported liquidity, $0.02 of daily volume and a price 1,700 times
off). BTC and ETH are measured through their wrapped forms on Ethereum, the deepest on-chain venue for each, and the answer says so.

The six entries are the contracts Hanko's registry names (``hanko/skills/exit_liquidity/venues.py``, MIT). Each address was confirmed by
the live test (``tests/test_live.py``), which reads the pool index for it and requires the base token of the pools to be that address.
"""

from __future__ import annotations

from typing import Any, Final

TOKENS: Final[dict[str, dict[str, Any]]] = {
    "SOL": {"chain": "solana", "address": "So11111111111111111111111111111111111111112", "decimals": 9, "via": "wrapped SOL"},
    "BONK": {"chain": "solana", "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "decimals": 5, "via": None},
    "JUP": {"chain": "solana", "address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", "decimals": 6, "via": None},
    "WIF": {"chain": "solana", "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "decimals": 6, "via": None},
    "ETH": {"chain": "ethereum", "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18, "via": "WETH on Ethereum"},
    "BTC": {"chain": "ethereum", "address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "decimals": 8, "via": "WBTC on Ethereum"},
}

USDC_SOLANA: Final = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# A pool whose other side is not a major quote asset is not depth a seller can use: a deep pool against a junk token adds nothing.
MAJOR_QUOTES: Final = frozenset({"USDC", "USDT", "SOL", "WSOL", "WETH", "ETH", "WBTC", "DAI"})

SUPPORTED: Final = tuple(TOKENS)
