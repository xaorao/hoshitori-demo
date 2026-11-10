"""Two sources that say the same thing about a price: do they agree?

RYO states a token's price in dollars and an exchange series states its last close. Neither is the other's interpretation, both are
observations, and nothing else in a cycle compares them: a stored number is grounded when it is the value that was stored, which says
nothing about whether another source says the same. This module records the comparison as an item of its own, derived from the two
readings, so that every arm and every receipt sees how far apart they were and whether that is within the tolerance.

The item states the two prices, the exchange, the gap as a share of the exchange's close, the tolerance and one word: ``AGREE`` when the
gap is no more than the tolerance and ``CONFLICT`` when it is more. It says nothing about which source is right. Where either price is
missing, is not a positive number, or the two are not about one asset, there is no item: not comparing is not agreeing.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Final

from hoshitori.evidence import DERIVED, EvidenceError, EvidenceItem, Kind

SOURCE: Final = "hoshitori.crosscheck"
TRANSFORM: Final = ("crosscheck", "1")
ADAPTER_VERSION: Final = "crosscheck-1"
TOLERANCE: Final = "0.02"  # a prior: two sources that state one price are within 2 percent of each other unless one is wrong
RYO_SOURCE: Final = "ryo"
RYO_PRICE: Final = "market.price_usd"
CLOSE: Final = "last_close"
_PLACES: Final = Decimal("0.000001")


def _number(text: object) -> Decimal | None:
    """A positive finite number written in text, or none."""
    if not isinstance(text, str):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def _field(item: EvidenceItem, name: str) -> Decimal | None:
    return _number(dict(item.fields).get(name))


def _asset(item: EvidenceItem) -> str | None:
    return item.asset_refs[0][0] if item.asset_refs else None


def price_check(
    ryo_item: EvidenceItem,
    ryo_digest: str,
    exchange_item: EvidenceItem,
    exchange_digest: str,
    built_at_ms: int,
    tolerance: str | Decimal = TOLERANCE,
) -> EvidenceItem | None:
    """The comparison of RYO's price with an exchange's last close, or none when they cannot be compared."""
    stated, close = _field(ryo_item, RYO_PRICE), _field(exchange_item, CLOSE)
    asset = _asset(ryo_item)
    if stated is None or close is None or asset is None or asset != _asset(exchange_item):
        return None
    limit = Decimal(tolerance)
    with localcontext() as ctx:
        ctx.prec = 60
        gap = abs(stated - close) / close
        shown = gap.quantize(_PLACES, rounding=ROUND_HALF_EVEN)
        within = gap <= limit
    parents = tuple(sorted(((ryo_item.evidence_id, ryo_digest), (exchange_item.evidence_id, exchange_digest))))
    events = [i.event_time_ms for i in (ryo_item, exchange_item) if i.event_time_ms is not None]
    try:
        return EvidenceItem(
            adapter_version=ADAPTER_VERSION,
            path=f"crosscheck/{ryo_item.path}",
            kind=Kind.DERIVED,
            source=SOURCE,
            origin=DERIVED,
            received_at_ms=built_at_ms,
            known_at_ms=built_at_ms,
            fields=tuple(
                sorted(
                    (
                        ("ryo_price_usd", format(stated, "f")),
                        ("exchange_close", format(close, "f")),
                        ("exchange", exchange_item.source),
                        ("difference", format(shown, "f")),
                        ("tolerance", format(limit.quantize(_PLACES, rounding=ROUND_HALF_EVEN), "f")),
                        ("state", "AGREE" if within else "CONFLICT"),
                    )
                )
            ),
            derived_from=parents,
            transform=TRANSFORM,
            data_mode=ryo_item.data_mode if ryo_item.data_mode == exchange_item.data_mode else "mixed",
            event_time_ms=min(events) if events else None,
            asset_refs=exchange_item.asset_refs,
        )
    except EvidenceError:
        return None


def candidate_checks(
    items: Iterable[tuple[EvidenceItem, str]], built_at_ms: int, tolerance: str | Decimal = TOLERANCE
) -> tuple[EvidenceItem, ...]:
    """The comparisons of one candidate's items: each RYO item that states a price against the newest exchange series."""
    held = list(items)
    prices = sorted(
        ((i, d) for i, d in held if i.source == RYO_SOURCE and i.kind is Kind.OBSERVATION and _field(i, RYO_PRICE) is not None),
        key=lambda pair: pair[0].path,
    )
    series = [(i, d) for i, d in held if i.source != RYO_SOURCE and i.kind is Kind.OBSERVATION and _field(i, CLOSE) is not None]
    if not prices or not series:
        return ()
    newest = min(series, key=lambda pair: (-(pair[0].event_time_ms if pair[0].event_time_ms is not None else -1), pair[0].evidence_id))
    made = (price_check(p, pd, newest[0], newest[1], built_at_ms, tolerance) for p, pd in prices)
    return tuple(item for item in made if item is not None)


def conflicts(items: Iterable[EvidenceItem]) -> tuple[str, ...]:
    """The ids of the price checks among ``items`` that found a conflict, in order."""
    return tuple(
        sorted(i.evidence_id for i in items if i.source == SOURCE and i.kind is Kind.DERIVED and dict(i.fields).get("state") == "CONFLICT")
    )
