"""The exchange venues the product reads, and how one is attached to the gateway.

``VENUES`` names each venue with its adapter and the host that serves its public market data. ``attach`` builds what the gateway needs for
one of them: the adapter, a governor sized from the limit the venue documents (``adapter.rate``), a set of breakers and the keyless
transport; it registers the first three with the gateway and returns the transport for the runner. A venue can be attached under another
provider name (the recorded replay of a venue is a provider of its own) and with another host (loopback in the tests; a mirror or a regional
host in use).

Hosts that are not the only choice:

* **Binance spot** also answers on ``data-api.binance.vision``, a market-data-only host (its time answer was recorded).
* **OKX** has a global host (``www.okx.com``, the default here) and regional ones (``eea.okx.com``, ``us.okx.com``) that serve the same
  prices but a different list of instruments. The global host is not answered from every network (the one this was built on blocks its
  name). Which host a deployment uses is part of the venue's identity and is chosen by the operator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
from hoshitori.clock import Clock
from hoshitori.gateway import Gateway
from hoshitori.governor import Governor
from hoshitori.venue_base import VenueAdapter, VenueConfig
from hoshitori.venue_binance import BinanceSpot, BinanceUsdm
from hoshitori.venue_coinbase import Coinbase
from hoshitori.venue_funding import BinanceFunding
from hoshitori.venue_hyperliquid import Hyperliquid
from hoshitori.venue_kraken import Kraken
from hoshitori.venue_okx import Okx
from hoshitori.venue_transport import VenueTransport


@dataclass(frozen=True)
class VenueSpec:
    adapter: type[VenueAdapter]
    base_url: str


VENUES: Final[Mapping[str, VenueSpec]] = {
    "binance-spot": VenueSpec(BinanceSpot, "https://api.binance.com"),
    "binance-usdm": VenueSpec(BinanceUsdm, "https://fapi.binance.com"),
    "coinbase": VenueSpec(Coinbase, "https://api.exchange.coinbase.com"),
    "kraken": VenueSpec(Kraken, "https://api.kraken.com"),
    "hyperliquid": VenueSpec(Hyperliquid, "https://api.hyperliquid.xyz"),
    "okx": VenueSpec(Okx, "https://www.okx.com"),
}


# Adapters of optional sources: they are attached the same way but are not exchanges to read candles and instruments from, so they
# have no place in VENUES (every venue there is held to the candle and instrument profile).
SOURCES: Final[Mapping[str, VenueSpec]] = {
    "binance-funding": VenueSpec(BinanceFunding, "https://fapi.binance.com"),
}


@dataclass(frozen=True)
class Attached:
    adapter: VenueAdapter
    governor: Governor
    breakers: BreakerSet
    transport: VenueTransport


def attach(
    gateway: Gateway,
    name: str,
    clock: Clock,
    *,
    base_url: str | None = None,
    provider: str | None = None,
    config: VenueConfig | None = None,
    breaker: BreakerConfig | None = None,
    concurrency: int = 2,
    allow_insecure_loopback: bool = False,
) -> Attached:
    """Register venue ``name`` with ``gateway`` and return its parts; the transport goes in the runner's table under its provider."""
    try:
        spec = {**VENUES, **SOURCES}[name]
    except KeyError:
        raise ValueError(f"{name!r} is not a venue (they are {sorted({**VENUES, **SOURCES})})") from None
    adapter = spec.adapter(config, provider)
    now = clock.now()
    governor = Governor(adapter.provider, adapter.rate.model, adapter.rate.limit, adapter.rate.window_s, now)
    settings = breaker or BreakerConfig()
    breakers = BreakerSet(Breaker(adapter.provider, settings, now), settings)
    gateway.register(adapter, governor, breakers, concurrency=concurrency)
    transport = VenueTransport(base_url or spec.base_url, adapter, clock.now, allow_insecure_loopback=allow_insecure_loopback)
    return Attached(adapter, governor, breakers, transport)


__all__ = ["SOURCES", "VENUES", "Attached", "VenueSpec", "attach"]
