"""Evidence items from what the gateway captured: RYO answers and exchange candles.

The readers that already exist (``ryo_envelope``, ``ryo_fields``, ``venue_data``) have decided what an answer says. This
module decides what the system *knows* from it: which of it is observation and which interpretation, what is unknown and why,
and when it was known.

* **RYO.** One answer becomes two items that share a capture: the observations (a measurement or an identity) and the
  interpretations (a verdict, a score, a plan, prose). An independent arm may read only the first. Nothing is converted to a
  number: a field that was null is unknown (``NULL``), one the contract names and the answer lacks is unknown (``ABSENT``),
  an empty text is unknown, and a zero is a measurement only if the answer was entirely available: in a degraded answer a
  zero may stand for "no value", so it is ``DEGRADED_ZERO``, and on a field that can never be zero (a price, a market
  capitalisation) it is ``MALFORMED``. Which section of an answer a field belongs to is not known for every tool, so
  "entirely available" is judged over the whole answer: status ok and every section available or not requested. This is
  stricter than it may need to be and errs towards unknown, never towards zero.
* **Exchange candles.** A series becomes one observation that records what was asked and what came back (the number of
  finished bars, the first and the last, the last close, how many were missing, still open or dropped as unfinished) and a
  hash of every bar. The bars themselves stay in the raw capture and in the series the settlement reads; the item is the
  dated, hashed statement that this was seen.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from hoshitori import canonical, jsonnum
from hoshitori.capture import RawCapture
from hoshitori.evidence import RYO as RYO_FAMILY
from hoshitori.evidence import EvidenceError, EvidenceItem, Family, FamilyKind, FieldValue, Kind, Redistribution, Unknown
from hoshitori.ryo_envelope import Envelope
from hoshitori.ryo_fields import FieldClass, Walked, classification_table
from hoshitori.timeline import to_ms
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_data import CandleSeries
from hoshitori.venue_funding import Funding

OK_WORDS: Final = frozenset({"available", "not_requested"})
_ZERO = re.compile(r"-?0(\.0+)?")
_INDEX = re.compile(r"\[\d+\]")
_KEY = re.compile(r"\{[^}]*\}")

# Fields that can never be zero: a price, a market capitalisation, a supply, a rank. A zero there is a malformed answer, not a measurement.
# The rest are signed measures, counts and ratios, where zero is a legitimate reading (NOT VERIFIED against the live catalogue for the
# two tools that have not answered yet).
RYO_ZERO_INVALID: Final = frozenset(
    {
        "asset.rank",
        "candidates[].market_cap_usd",
        "candidates[].price_usd",
        "market.circulating_supply",
        "market.fdv_usd",
        "market.fully_diluted_value_usd",
        "market.market_cap_usd",
        "market.mcap_usd",
        "market.price_usd",
        "market.total_market_cap_usd",
        "tokens[].metrics.market_cap_usd",
        "tokens[].price_usd",
        "tokens[].rank",
        "top_movers.gainers[].price_usd",
        "top_movers.losers[].price_usd",
    }
)


class SourceError(ValueError):
    """An answer or a series that cannot be turned into evidence."""


def pattern_of(path: str) -> str:
    """A concrete field path as the specification writes it: indexes and keys replaced by ``[]`` and ``{}``."""
    return _KEY.sub("{}", _INDEX.sub("[]", path))


def _json(value: Any) -> canonical.JsonValue:
    """A request argument as canonical data: numbers become integers or plain decimal text, never floats."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, jsonnum.Num):
        plain = value.plain()
        return int(plain) if re.fullmatch(r"-?[0-9]{1,15}", plain) else plain
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    raise SourceError(f"a request value of type {type(value).__name__} cannot be recorded")


@dataclass(frozen=True)
class RyoEvidence:
    observation: EvidenceItem
    interpretation: EvidenceItem


def ryo_items(capture: RawCapture, envelope: Envelope, walked: Walked, adapter_version: str) -> RyoEvidence:
    """The observation item and the interpretation item of one RYO answer (either may hold nothing but what is unknown).

    A replayed answer (the recorded transport marks its capture's request ``mode`` as ``recorded``) says ``recorded`` as its data mode:
    its body still says ``live``, because a body is evidence and is never edited, and an item must never let a replay pass as a read."""
    all_ok = envelope.status == "ok" and all(word in OK_WORDS for word in envelope.availability.values())
    patterns = {row[0]: row[1] for row in classification_table(envelope.tool)}
    values: dict[FieldClass, list[tuple[str, FieldValue]]] = {FieldClass.OBSERVATION: [], FieldClass.INTERPRETATION: []}
    unknown: dict[FieldClass, list[tuple[str, Unknown]]] = {FieldClass.OBSERVATION: [], FieldClass.INTERPRETATION: []}
    for item in walked.fields:
        if item.klass is FieldClass.META:
            continue
        if item.value is None:
            unknown[item.klass].append((item.path, Unknown.NULL))
        elif item.value == "":
            unknown[item.klass].append((item.path, Unknown.ABSENT))
        elif item.kind == "number" and isinstance(item.value, str) and _ZERO.fullmatch(item.value):
            if pattern_of(item.path) in RYO_ZERO_INVALID:
                unknown[item.klass].append((item.path, Unknown.MALFORMED))
            elif not all_ok:
                unknown[item.klass].append((item.path, Unknown.DEGRADED_ZERO))
            else:
                values[item.klass].append((item.path, item.value))
        else:
            values[item.klass].append((item.path, item.value))
    for path in walked.missing:
        for klass in _classes_under(pattern_of(path), patterns):
            unknown[klass].append((path, Unknown.ABSENT))
    request = tuple(sorted((str(key), _json(value)) for key, value in envelope.request.items()))
    received = to_ms(capture.received_at)
    common: dict[str, Any] = {
        "adapter_version": adapter_version,
        "source": capture.source,
        "origin": RYO_FAMILY,
        "received_at_ms": received,
        "known_at_ms": received,
        "capture_id": capture.capture_id,
        "raw_sha256": capture.body_sha256,
        "tool": envelope.tool,
        "request": request,
        "status": envelope.status,
        "data_mode": "recorded" if capture.request.get("mode") == "recorded" else envelope.data_mode,
        "availability": tuple(sorted(envelope.availability.items())),
        "warnings": envelope.warnings,
        "event_time_ms": to_ms(envelope.as_of),
        "redistribution": Redistribution.PRIVATE_ONLY,  # until the organisers say whether RYO responses may be shown
    }

    def build(path: str, kind: Kind, klass: FieldClass) -> EvidenceItem:
        return EvidenceItem(
            path=path, kind=kind, fields=tuple(sorted(values[klass])), unknown_fields=tuple(sorted(unknown[klass])), **common
        )

    try:
        observation = build("observation", Kind.OBSERVATION, FieldClass.OBSERVATION)
        interpretation = build("interpretation", Kind.INTERPRETATION, FieldClass.INTERPRETATION)
    except EvidenceError as exc:
        raise SourceError(f"the answer cannot be recorded as evidence: {exc}") from exc
    return RyoEvidence(observation, interpretation)


def _classes_under(pattern: str, patterns: Mapping[str, str]) -> set[FieldClass]:
    """The kinds of field a missing member would have held. An object may hold both kinds, and then it is absent from both items.

    A member the table does not know at all is taken for an observation, so that nothing is hidden from an arm by a gap in the table.
    """
    under = {
        klass for leaf, klass in patterns.items() if leaf == pattern or leaf.startswith((f"{pattern}.", f"{pattern}[", f"{pattern}{{"))
    }
    if not under:
        return {FieldClass.OBSERVATION}
    return {FieldClass(klass) for klass in under} - {FieldClass.META}


def bars_digest(series: CandleSeries) -> str:
    """SHA-256 of every finished bar of a series, canonical, so the item can be checked against the bars it speaks of."""
    bars: list[canonical.JsonValue] = []
    for candle in series.candles:
        bar: dict[str, canonical.JsonValue] = {
            "open_ms": to_ms(candle.open_time),
            "close_ms": to_ms(candle.close_time),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
        }
        for name, value in (("volume", candle.volume), ("quote_volume", candle.quote_volume)):
            if value is not None:
                bar[name] = value
        if candle.trades is not None:
            bar["trades"] = candle.trades
        bars.append(bar)
    return hashlib.sha256(canonical.dumps({"bars": bars})).hexdigest()


def _stamp(moment: Any) -> str:
    return format_rfc3339_ms(moment)


def series_item(capture: RawCapture, series: CandleSeries, adapter_version: str, data_mode: str = "live") -> EvidenceItem:
    """The observation item of one candle series read from an exchange; ``data_mode`` says whether the read was live or a replay."""
    try:
        origin = Family(FamilyKind.EXCHANGE, series.venue)
    except EvidenceError as exc:
        raise SourceError(f"the venue {series.venue!r} cannot be an origin family: {exc}") from exc
    fields: dict[str, FieldValue] = {
        "bars_sha256": bars_digest(series),
        "count": str(len(series.candles)),
        "excluded_unfinished": str(series.excluded_unfinished),
        "interval_s": str(series.interval_s),
        "missing": str(len(series.missing)),
        "pending": str(len(series.pending)),
        "server_time": _stamp(series.server_time),
    }
    unknown: list[tuple[str, Unknown]] = []
    if series.candles:
        first, last = series.candles[0], series.candles[-1]
        fields["first_open"] = _stamp(first.open_time)
        fields["last_close"] = last.close
        fields["last_close_time"] = _stamp(last.close_time)
        fields["last_open"] = _stamp(last.open_time)
    else:
        unknown = [(name, Unknown.ABSENT) for name in ("first_open", "last_close", "last_close_time", "last_open")]
    received = to_ms(capture.received_at)
    try:
        return EvidenceItem(
            adapter_version=adapter_version,
            path="series",
            kind=Kind.OBSERVATION,
            source=capture.source,
            origin=origin,
            received_at_ms=received,
            known_at_ms=received,
            fields=tuple(sorted(fields.items())),
            unknown_fields=tuple(unknown),
            capture_id=capture.capture_id,
            raw_sha256=capture.body_sha256,
            request=(
                ("end", _stamp(series.end)),
                ("instrument", series.instrument),
                ("interval_s", series.interval_s),
                ("start", _stamp(series.start)),
            ),
            data_mode=data_mode,
            event_time_ms=to_ms(series.server_time),  # the series is the venue's state as of its own clock, not as old as its newest bar
            redistribution=Redistribution.PRIVATE_ONLY,  # until the venue's terms are read
        )
    except EvidenceError as exc:
        raise SourceError(f"the series cannot be recorded as evidence: {exc}") from exc


def funding_item(capture: RawCapture, funding: Funding, adapter_version: str, data_mode: str = "live") -> EvidenceItem:
    """The observation item of one perpetual's funding reading. The rate is the last settled one, as the venue states it; the item
    holds the rate, the mark and index prices and the next settlement time as the venue gave them, and computes nothing."""
    try:
        origin = Family(FamilyKind.EXCHANGE, funding.venue)
    except EvidenceError as exc:
        raise SourceError(f"the venue {funding.venue!r} cannot be an origin family: {exc}") from exc
    fields: dict[str, FieldValue] = {
        "last_funding_rate": funding.rate,
        "next_funding_time": _stamp(funding.next_funding_time),
        "mark_price": funding.mark_price,
        "index_price": funding.index_price,
        "server_time": _stamp(funding.server_time),
    }
    received = to_ms(capture.received_at)
    try:
        return EvidenceItem(
            adapter_version=adapter_version,
            path="funding",
            kind=Kind.OBSERVATION,
            source=capture.source,
            origin=origin,
            received_at_ms=received,
            known_at_ms=received,
            fields=tuple(sorted(fields.items())),
            capture_id=capture.capture_id,
            raw_sha256=capture.body_sha256,
            request=(("instrument", funding.instrument),),
            data_mode=data_mode,
            event_time_ms=to_ms(funding.server_time),
            redistribution=Redistribution.PRIVATE_ONLY,  # until the venue's terms are read
        )
    except EvidenceError as exc:
        raise SourceError(f"the funding reading cannot be recorded as evidence: {exc}") from exc
