"""Which two instruments settle a token: the protocol's primary and second oracle, found in the asset registry.

``CYCLE_RUNNER_SPEC`` sections 2 and 3 and contract section 7 are the rules. A token can be a forecast candidate only if its primary
oracle (one instrument of the protocol's kind and quote class on the primary venue) and its second oracle (the same on the second venue)
are both there and trading. If a venue lists the token more than once in that class, or not at all, there is no oracle: nothing is
guessed from a name. The asset id of the candidate is the asset id of the primary instrument, which is also what a forecast stores.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from hoshitori.manifest import Resolved
from hoshitori.protocol import Protocol
from hoshitori.registry import MappingStatus, Registry, Want
from hoshitori.venue_data import Instrument, InstrumentKind, InstrumentStatus


@dataclass(frozen=True)
class Pick:
    """The settlement pair of one token."""

    token_id: str
    asset_id: str  # the asset of the primary instrument
    primary_id: str
    second_id: str
    primary: Instrument
    second: Instrument


def _one(registry: Registry, protocol: Protocol, ids: tuple[str, ...], venue: str, as_of: datetime) -> tuple[str, Instrument] | None:
    found = []
    for instrument_id in ids:
        instrument = registry.instrument_at(instrument_id, as_of)
        if (
            instrument is not None
            and instrument.venue == venue
            and instrument.kind.value == protocol.instrument_kind
            and instrument.quote_class == protocol.quote_class
        ):
            found.append((instrument_id, instrument))
    return found[0] if len(found) == 1 else None


def pick(registry: Registry, protocol: Protocol, token_id: str, as_of: datetime, *, trading: bool = True) -> Pick | None:
    """The primary and second oracle of a token at ``as_of``, or ``None`` if either is missing or ambiguous, or (unless ``trading`` is
    false, which settlement uses: a halted market still has the candles of the minutes that matter) not trading."""
    ids = registry.instruments_of(token_id, as_of)
    primary = _one(registry, protocol, ids, protocol.primary_venue, as_of)
    second = _one(registry, protocol, ids, protocol.second_venue, as_of)
    if primary is None or second is None:
        return None
    if trading and (primary[1].status is not InstrumentStatus.TRADING or second[1].status is not InstrumentStatus.TRADING):
        return None
    want = Want(token_id=token_id, kind=InstrumentKind(protocol.instrument_kind), quote_class=protocol.quote_class)
    joined = registry.join(primary[0], want, as_of)
    if joined.asset is None:  # a join that is not OK carries no asset
        return None
    return Pick(token_id, joined.asset.asset_id, primary[0], second[0], primary[1], second[1])


BASIS_VENUES: Final = (
    "coinbase",
    "kraken",
)  # where the dollar price of the stable coin is read: the exchanges of the dollar-quoted pair (contract section 7)


def stable_basis_instrument(registry: Registry, venues: Collection[str], as_of: datetime) -> tuple[str, str] | None:
    """The (venue, instrument) that tells how far the stable coin the league settles in is from the dollar: the spot pair of USDT quoted in
    US dollars on the first of ``BASIS_VENUES`` that is connected and lists exactly one, as the registry knew it at ``as_of``; none if the
    token is not resolved or no such pair is listed (the basis is then not known, and no flag is claimed either way)."""
    resolution = registry.resolve_symbol("USDT", as_of=as_of)
    if resolution.status is not MappingStatus.RESOLVED:
        return None
    ids = registry.instruments_of(resolution.token_id, as_of)
    for venue in BASIS_VENUES:
        if venue not in venues:
            continue
        found = []
        for instrument_id in ids:
            instrument = registry.instrument_at(instrument_id, as_of)
            if (
                instrument is not None
                and instrument.venue == venue
                and instrument.kind is InstrumentKind.SPOT
                and instrument.quote_class == "USD"
                and instrument.status is InstrumentStatus.TRADING
            ):
                found.append(instrument.raw_id)
        if len(found) == 1:
            return venue, found[0]
    return None


def pick_sealed(registry: Registry, protocol: Protocol, asset_id: str, sealed: datetime, until: datetime) -> Pick | None:
    """The settlement pair of a forecast's asset as the registry knew it when the forecast was sealed.

    The pair is looked up at the seal, never at the time of settlement, so a listing change made afterwards cannot re-point a forecast.
    It stands unless an instrument had left the list or been delisted by ``until``, the end of the forecast's own window (its origin plus
    its horizon): a delisting that the registry learned of after that does not take away a forecast that could be settled. ``None`` is a
    pair that cannot be settled, and the forecast is void as delisted.
    """
    found = pick_for_asset(registry, protocol, asset_id, sealed, trading=False)
    if found is None:
        return None
    for instrument_id in (found.primary_id, found.second_id):
        at_end = registry.instrument_at(instrument_id, until)
        if at_end is None or at_end.status is InstrumentStatus.DELISTED:
            return None
    return found


def resolver(registry: Registry, protocol: Protocol, as_of: datetime) -> Callable[[str], Resolved]:
    """What the manifest asks of the registry for a symbol: its asset, whether the mapping is unambiguous, whether it can be settled."""

    def resolve(symbol: str) -> Resolved:
        resolution = registry.resolve_symbol(symbol, as_of=as_of)
        if resolution.status is not MappingStatus.RESOLVED:
            return Resolved(None, False, False)
        found = pick(registry, protocol, resolution.token_id, as_of)
        if found is not None:
            return Resolved(found.asset_id, True, True)
        primary = _one(registry, protocol, registry.instruments_of(resolution.token_id, as_of), protocol.primary_venue, as_of)
        if primary is None:
            return Resolved(None, True, False)
        joined = registry.join(primary[0], Want(token_id=resolution.token_id), as_of)
        return Resolved(joined.asset.asset_id if joined.asset is not None else None, True, False)

    return resolve


def pick_for_asset(registry: Registry, protocol: Protocol, asset_id: str, as_of: datetime, *, trading: bool = True) -> Pick | None:
    """The settlement pair of the token an asset id names, if the asset is still the primary oracle of its token."""
    asset = registry.lookup_asset(asset_id, as_of)
    if asset is None:
        return None
    found = pick(registry, protocol, asset.token_id, as_of, trading=trading)
    return found if found is not None and found.asset_id == asset_id else None
