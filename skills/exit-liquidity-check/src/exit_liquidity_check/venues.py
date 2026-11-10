"""Venue evidence: real pool depth from DexScreener and, for Solana tokens, an executable Jupiter quote.

Adapted in structure from ``hanko/skills/exit_liquidity/venues.py`` (IamHarrie-Labs/hanko, MIT, Copyright (c) 2026 Harrie; see NOTICE.md).
Parsing is pure (a payload in, evidence out) and is tested offline on recorded replies; fetching goes through ``transport.Caller`` so it has
the budget, retries, cool-down and provenance of every other call.

Two traps and how each is met:

* Impostor pools. Pools are found by canonical address (``registry``), never by symbol, and a pool is rejected, with its reason
  recorded, when the token is its quote side, it reports no liquidity, did not trade in 24 hours, has no price, its other side is not a
  major quote asset, or its price is more than 3 percent from the volume-weighted median.
* A model mistaken for a measurement. The pool figures feed the constant-product model; the Jupiter quote is observed. They are kept apart
  all the way through, and when both exist the answer shows how far the model was from the quote.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from statistics import median
from typing import Any, Final

from .errors import SourceError
from .registry import MAJOR_QUOTES, TOKENS, USDC_SOLANA
from .transport import Caller

DEXSCREENER_PAIRS: Final = "https://api.dexscreener.com/token-pairs/v1/{chain}/{address}"
JUPITER_QUOTE: Final = "https://lite-api.jup.ag/swap/v1/quote"
PRICE_TOLERANCE: Final = 0.03
REFERENCE_QUOTE_USD: Final = 100.0
QUOTE_SLIPPAGE_BPS: Final = 300  # the slippage ceiling asked of the router; the answer reports the cost actually quoted, not this


@dataclass(frozen=True, slots=True)
class Pool:
    dex: str
    pair: str
    quote_symbol: str
    liquidity_usd: float
    volume_24h_usd: float
    price_usd: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "dex": self.dex,
            "pair": self.pair,
            "quote": self.quote_symbol,
            "liquidity_usd": round(self.liquidity_usd, 2),
            "volume_24h_usd": round(self.volume_24h_usd, 2),
            "price_usd": self.price_usd,
        }


@dataclass(frozen=True, slots=True)
class PoolDepth:
    """The on-chain depth a seller can reach, and what was left out and why."""

    token: str
    chain: str
    address: str
    via: str | None
    pools: tuple[Pool, ...]  # the accepted pools, deepest first
    rejected: tuple[tuple[str, str], ...]  # (pair address, reason)
    price_usd: float | None  # the volume-weighted median price of the accepted pools
    total_liquidity_usd: float | None
    total_volume_24h_usd: float | None

    @property
    def source(self) -> str:
        return f"dexscreener:{self.chain}/{self.address}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "chain": self.chain,
            "address": self.address,
            "via": self.via,
            "deepest_pool": self.pools[0].as_dict() if self.pools else None,
            "pools_used": len(self.pools),
            "pools_rejected": [{"pair": p, "reason": r} for p, r in self.rejected],
            "price_usd": self.price_usd,
            "total_liquidity_usd": None if self.total_liquidity_usd is None else round(self.total_liquidity_usd, 2),
            "total_volume_24h_usd": None if self.total_volume_24h_usd is None else round(self.total_volume_24h_usd, 2),
            "source": self.source,
        }


def weighted_median(values: list[tuple[float, float]]) -> float:
    """The median of ``value`` weighted by ``weight`` (a plain median when the weights sum to nothing)."""
    total = sum(w for _, w in values)
    if total <= 0:
        return float(median(v for v, _ in values))
    acc = 0.0
    for value, weight in sorted(values):
        acc += weight
        if acc >= total / 2:
            return value
    return sorted(values)[-1][0]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        out = float(value)
    except ValueError:
        return None
    return out if out == out and abs(out) != float("inf") else None


def parse_pairs(token: str, payload: Any) -> PoolDepth:
    """A DexScreener token-pairs payload in, the filtered depth out; a payload that is not a list is a ``bad_response`` ``SourceError``."""
    spec = TOKENS[token]
    address = spec["address"]
    if not isinstance(payload, list):
        raise SourceError("bad_response")
    candidates: list[Pool] = []
    rejected: list[tuple[str, str]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        pair = str(raw.get("pairAddress") or "?")
        base = raw.get("baseToken") or {}
        quote = raw.get("quoteToken") or {}
        if str(base.get("address", "")).lower() != address.lower():
            rejected.append((pair, "the token is the quote side, not the base"))
            continue
        quote_symbol = str(quote.get("symbol") or "?")
        liq = _number((raw.get("liquidity") or {}).get("usd"))
        vol = _number((raw.get("volume") or {}).get("h24"))
        price = _number(raw.get("priceUsd"))
        if liq is None or liq <= 0:
            rejected.append((pair, "no reported liquidity"))
        elif vol is None or vol <= 0:
            rejected.append((pair, "did not trade in the last 24 hours"))
        elif price is None or price <= 0:
            rejected.append((pair, "no usable price"))
        elif quote_symbol.upper() not in MAJOR_QUOTES:
            rejected.append((pair, f"the other side ({quote_symbol}) is not a major quote asset"))
        else:
            candidates.append(Pool(str(raw.get("dexId") or "?"), pair, quote_symbol, liq, vol, price))
    if not candidates:
        return PoolDepth(token, spec["chain"], address, spec["via"], (), tuple(rejected), None, None, None)
    mid = weighted_median([(p.price_usd, p.volume_24h_usd) for p in candidates])
    used: list[Pool] = []
    for pool in candidates:
        if abs(pool.price_usd - mid) / mid > PRICE_TOLERANCE:
            rejected.append(
                (pool.pair, f"price {pool.price_usd:.6g} is more than {round(PRICE_TOLERANCE * 100)}% from the median {mid:.6g}")
            )
        else:
            used.append(pool)
    used.sort(key=lambda p: (-p.liquidity_usd, p.pair))
    return PoolDepth(
        token,
        spec["chain"],
        address,
        spec["via"],
        tuple(used),
        tuple(rejected),
        mid,
        sum(p.liquidity_usd for p in used) if used else None,
        sum(p.volume_24h_usd for p in used) if used else None,
    )


@dataclass(frozen=True, slots=True)
class VenueQuote:
    """What Jupiter would pay for selling the size, at the moment of the call."""

    token: str
    size_usd: float
    in_amount: int
    out_usd: float
    effective_cost: float  # 1 - out / (in at the near-mid rate): price impact plus the venue's fees
    venue_price_impact: float | None
    route_hops: int

    @property
    def source(self) -> str:
        return f"jupiter:{TOKENS[self.token]['address']}->USDC"

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": "jupiter",
            "basis": "quoted",
            "size_usd": round(self.size_usd, 2),
            "out_usd": round(self.out_usd, 2),
            "effective_cost_pct": round(self.effective_cost * 100, 4),
            "venue_price_impact_pct": None if self.venue_price_impact is None else round(self.venue_price_impact * 100, 4),
            "route_hops": self.route_hops,
            "source": self.source,
        }


def _amount(payload: Any, key: str) -> int:
    if not isinstance(payload, dict):
        raise SourceError("bad_response")
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SourceError("bad_response")
    try:
        out = int(value)
    except ValueError as exc:
        raise SourceError("bad_response") from exc
    return out


def parse_quote(token: str, reference: Any, sized: Any, *, size_usd: float) -> VenueQuote:
    """Two Jupiter quotes in (a small reference and the real size), the true cost of the exit out.

    The cost is measured against Jupiter's own near-mid rate from the small quote, not against another source's price, so a stale price
    elsewhere cannot move the figure. It includes the venue's fees, which the constant-product model leaves out."""
    ref_in, ref_out = _amount(reference, "inAmount"), _amount(reference, "outAmount")
    size_in, size_out = _amount(sized, "inAmount"), _amount(sized, "outAmount")
    if ref_in <= 0 or size_in <= 0 or ref_out <= 0 or size_out <= 0:
        raise SourceError("bad_response")
    fair = size_in * (ref_out / ref_in)
    cost = max(0.0, 1.0 - size_out / fair)
    impact = _number(sized.get("priceImpactPct"))
    route = sized.get("routePlan")
    return VenueQuote(token, size_usd, size_in, size_out / 1e6, cost, impact, len(route) if isinstance(route, list) else 0)


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError as exc:
        raise SourceError("bad_response") from exc


def fetch_depth(token: str, caller: Caller) -> PoolDepth:
    spec = TOKENS[token]
    url = DEXSCREENER_PAIRS.format(chain=spec["chain"], address=spec["address"])
    reply = caller.request("dexscreener", "GET", url)
    return parse_pairs(token, _json(reply.body))


def fetch_quote(token: str, size_usd: float, price_usd: float, caller: Caller) -> VenueQuote:
    """A small reference quote and a full-size quote from Jupiter (Solana tokens only)."""
    spec = TOKENS[token]
    if spec["chain"] != "solana" or price_usd <= 0 or size_usd <= 0:
        raise ValueError("a quote needs a Solana token, a size and a price")
    scale = 10 ** spec["decimals"]
    payloads: list[Any] = []
    for usd in (REFERENCE_QUOTE_USD, size_usd):
        amount = max(1, int(usd / price_usd * scale))
        query = f"?inputMint={spec['address']}&outputMint={USDC_SOLANA}&amount={amount}&slippageBps={QUOTE_SLIPPAGE_BPS}"
        payloads.append(_json(caller.request("jupiter", "GET", JUPITER_QUOTE + query).body))
    return parse_quote(token, payloads[0], payloads[1], size_usd=size_usd)
