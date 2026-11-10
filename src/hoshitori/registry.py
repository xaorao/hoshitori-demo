"""The asset registry: which token an exchange instrument is, which instruments a token has, and when we knew it.

``ASSET_REGISTRY_SPEC`` is the structure; this module is the state machine. A ticker is never a join key. Two things are kept apart:

* a **token** (what a news item, a post or RYO is talking about), named by its symbol and a *series* (a symbol that ended and came back is a
  new series, never carried over), and
* an **instrument** (the series we score and fill against), named by the venue's own id and kind (``Instrument.instrument_id``).

An instrument maps to a token with a status: ``RESOLVED`` (no conflict, and two independent sources agree), ``AMBIGUOUS`` (a conflict) or
``UNKNOWN`` (one source or none). Evidence is joined only across ``RESOLVED`` mappings, and only when the token, the kind, the quote class
and the multiplier the evidence was measured with are the instrument's own (``join``).

The registry is event-sourced. A venue's list is *admitted* (``plan_snapshot``): a list that is empty, shrank past a fraction, shows a surge
of unmapped entries or breaks its own scope is rejected, the previous list stays current and the rejection is a record. An accepted list
becomes instrument events (added, changed, removed), the diff against the previous list, plus one snapshot event. The records are plain
canonical dictionaries for the ledger; ``Registry.replay`` rebuilds the same state from them. Every record extends a hash chain, and the
head is the *snapshot hash* a decision stores, so a join can be made against the registry as it was when the evidence was taken.

Knowledge time is what the registry has: a row is visible from the instant we learned it, and every record has an instant of its own. An
"as of" read returns only what was known by then. The registry holds no file and no clock, is single-threaded and never calls the network.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from hoshitori import canonical
from hoshitori.ids import natural_id
from hoshitori.registry_aliases import ALIASES
from hoshitori.timeutil import ensure_utc, format_rfc3339_ms, parse_rfc3339
from hoshitori.typed import hidden_character
from hoshitori.venue_data import Instrument, InstrumentKind, InstrumentList, InstrumentStatus, token_symbol

SPEC: Final = "hoshitori.registry/1"
SCOPES: Final = ("all", "spot", "perpetual", "future")
SYMBOL: Final = re.compile(r"[^\s\x00-\x1f\x7f]{1,40}")


def _looks_like_another(text: str) -> bool:
    """A symbol that shows nothing in part, or writes a Latin letter beside a Cyrillic or a Greek one: it reads as another symbol's name."""
    if hidden_character(text) is not None:
        return True
    scripts = {unicodedata.name(c, "").split(" ", 1)[0] for c in text if c.isalpha()}
    return "LATIN" in scripts and bool(scripts & {"CYRILLIC", "GREEK"})


CHAIN_ID: Final = re.compile(r"[a-z0-9]{1,16}(:[A-Za-z0-9_-]{1,40})?")
CONTRACT: Final = re.compile(r"0x[0-9a-fA-F]{40}|[A-Za-z0-9_.:-]{8,128}")
INSTRUMENT_KEYS: Final = frozenset(
    {
        "venue",
        "raw_id",
        "kind",
        "base_raw",
        "quote_raw",
        "quote_class",
        "multiplier",
        "settle",
        "tick_size",
        "lot_size",
        "min_notional",
        "status",
        "contract_expiry",
        "base_ref",
    }
)


class RegistryError(ValueError):
    """A registry record that cannot be applied: it breaks the order, names something that is not there or is not well formed."""


class MappingStatus(StrEnum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class Basis(StrEnum):
    CONTRACT = "CONTRACT"  # a source named the contract and an exchange lists the symbol
    CURATED = "CURATED"  # two operators agree, one of them through a curated spelling
    VENUES = "VENUES"  # two operators agree on the ticker alone (weak: it is the same name, not proof of the same token)
    SINGLE_SOURCE = "SINGLE_SOURCE"
    NO_LISTING = "NO_LISTING"
    CLAIM_CONFLICT = "CLAIM_CONFLICT"  # sources name different contracts for the symbol
    REF_CONFLICT = "REF_CONFLICT"  # one venue has two different tokens of this name
    DUPLICATE_PAIR = "DUPLICATE_PAIR"  # one venue has two different instruments for one pair


class JoinCode(StrEnum):
    OK = "OK"
    UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT"
    SNAPSHOT_MISMATCH = "SNAPSHOT_MISMATCH"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


@dataclass(frozen=True)
class RegistryConfig:
    max_shrink: float = 0.2  # a list that loses more than this share of its instruments is rejected
    min_list_for_shrink: int = 10  # a smaller list is not judged by its shrink
    max_unmapped_share: float = 0.05
    unmapped_allowance: int = 10  # a few unmapped entries are normal on any list

    def __post_init__(self) -> None:
        if (
            not 0 < self.max_shrink < 1
            or not 0 <= self.max_unmapped_share < 1
            or self.min_list_for_shrink < 1
            or self.unmapped_allowance < 0
        ):
            raise RegistryError("the registry configuration is not valid")


@dataclass(frozen=True)
class Claim:
    """A source says this symbol is this contract on this chain. Two sources that agree are evidence; two that differ are a conflict."""

    symbol: str
    source: str
    chain_id: str
    contract_address: str

    def __post_init__(self) -> None:
        if SYMBOL.fullmatch(self.symbol) is None or _looks_like_another(self.symbol) or self.symbol != self.symbol.upper():
            raise RegistryError("a claim names its symbol in upper case")
        if not self.source or self.source != self.source.strip() or len(self.source) > 80:
            raise RegistryError("a claim names its source")
        if CHAIN_ID.fullmatch(self.chain_id) is None:
            raise RegistryError("a claim names its chain")
        if CONTRACT.fullmatch(self.contract_address) is None:
            raise RegistryError("a claim names a contract address")
        if self.contract_address.startswith("0x") and self.contract_address != self.contract_address.lower():
            raise RegistryError("an address in hexadecimal is lower case")

    def as_record(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "source": self.source, "chain_id": self.chain_id, "contract_address": self.contract_address}


@dataclass(frozen=True)
class Row:
    instrument: Instrument
    scope: str
    symbol: str
    series: int
    generation: int  # 1 for the first time we saw the instrument; a return after the end is a new generation
    known_from: datetime
    known_to: datetime | None


@dataclass(frozen=True)
class MappingRow:
    instrument_id: str
    symbol: str
    series: int
    token_id: str
    status: MappingStatus
    basis: Basis
    evidence: tuple[str, ...]
    candidates: tuple[str, ...]
    multiplier: str
    known_from: datetime
    known_to: datetime | None


@dataclass(frozen=True)
class Resolution:
    symbol: str
    status: MappingStatus
    basis: Basis
    token_id: str
    candidates: tuple[str, ...]
    evidence: tuple[str, ...]
    instrument_ids: tuple[str, ...]

    @property
    def joinable(self) -> bool:
        """Evidence is joined across this resolution only if it is resolved; anything else is kept apart and labelled."""
        return self.status is MappingStatus.RESOLVED


@dataclass(frozen=True)
class Asset:
    asset_id: str
    token_id: str
    instrument_id: str
    venue: str
    raw_id: str
    kind: InstrumentKind
    base_raw: str
    quote_raw: str
    quote_class: str
    multiplier: str
    settle: str | None
    snapshot_hash: str


@dataclass(frozen=True)
class Want:
    """What the evidence was measured on. A field left ``None`` is not checked; the multiplier is the scale applied to the series."""

    multiplier: str = "1"
    token_id: str | None = None
    kind: InstrumentKind | None = None
    quote_class: str | None = None
    snapshot_hash: str | None = None


@dataclass(frozen=True)
class JoinResult:
    code: JoinCode
    field: str | None
    detail: str
    asset: Asset | None

    @property
    def ok(self) -> bool:
        return self.code is JoinCode.OK


@dataclass(frozen=True)
class _ListState:
    accepted_at: datetime  # when the list now current was accepted
    count: int
    list_hash: str
    unmapped: tuple[tuple[str | None, str], ...]


def operator_of(venue: str) -> str:
    """The operator behind a venue name: ``binance-spot`` and ``binance-usdm`` are one operator and count as one source."""
    return venue.partition("-")[0]


def canonical_symbol(text: str) -> str:
    if not isinstance(text, str) or SYMBOL.fullmatch(text) is None or _looks_like_another(text):
        raise RegistryError("a symbol is 1 to 40 visible characters with no space")
    return text.upper()


def _floor_ms(moment: datetime) -> datetime:
    moment = ensure_utc(moment)
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def instrument_record(instrument: Instrument) -> dict[str, Any]:
    return {
        "venue": instrument.venue,
        "raw_id": instrument.raw_id,
        "kind": instrument.kind.value,
        "base_raw": instrument.base_raw,
        "quote_raw": instrument.quote_raw,
        "quote_class": instrument.quote_class,
        "multiplier": instrument.multiplier,
        "settle": instrument.settle,
        "tick_size": instrument.tick_size,
        "lot_size": instrument.lot_size,
        "min_notional": instrument.min_notional,
        "status": instrument.status.value,
        "contract_expiry": None if instrument.contract_expiry is None else format_rfc3339_ms(instrument.contract_expiry),
        "base_ref": instrument.base_ref,
    }


def _field(record: Mapping[str, Any], name: str, *, optional: bool = False) -> str | None:
    value = record[name]
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{name} is missing or not text")
    return value


def instrument_from_record(record: Mapping[str, Any]) -> Instrument:
    if not isinstance(record, dict) or set(record) != INSTRUMENT_KEYS:
        raise RegistryError("an instrument record has exactly its fields")
    expiry = _field(record, "contract_expiry", optional=True)
    try:
        return Instrument(
            venue=str(_field(record, "venue")),
            raw_id=str(_field(record, "raw_id")),
            kind=InstrumentKind(record["kind"]),
            base_raw=str(_field(record, "base_raw")),
            quote_raw=str(_field(record, "quote_raw")),
            quote_class=str(_field(record, "quote_class")),
            multiplier=str(_field(record, "multiplier")),
            settle=_field(record, "settle", optional=True),
            tick_size=_field(record, "tick_size", optional=True),
            lot_size=_field(record, "lot_size", optional=True),
            min_notional=_field(record, "min_notional", optional=True),
            status=InstrumentStatus(record["status"]),
            contract_expiry=None if expiry is None else parse_rfc3339(expiry),
            base_ref=_field(record, "base_ref", optional=True),
        )
    except (ValueError, TypeError) as exc:
        raise RegistryError(f"an instrument record is not valid: {exc}") from exc


def _list_hash(instruments: Iterable[Instrument], unmapped: Iterable[tuple[str | None, str]]) -> str:
    body: dict[str, Any] = {
        "instruments": [instrument_record(i) for i in sorted(instruments, key=lambda i: i.instrument_id)],
        "unmapped": [[raw, reason] for raw, reason in sorted(unmapped, key=lambda u: (u[0] or "", u[1]))],
    }
    return hashlib.sha256(canonical.dumps(body)).hexdigest()


def _scope_allows(scope: str, instrument: Instrument) -> bool:
    return scope == "all" or instrument.kind.value == scope


class Registry:
    def __init__(self, config: RegistryConfig | None = None, aliases: Mapping[str, Mapping[str, str]] | None = None) -> None:
        self.config = config or RegistryConfig()
        self._aliases = {operator: dict(table) for operator, table in (ALIASES if aliases is None else aliases).items()}
        for operator, table in self._aliases.items():
            for spelling, target in table.items():
                if spelling != spelling.upper() or target != target.upper() or spelling == target:
                    raise RegistryError(f"alias {operator}:{spelling} is not an upper-case spelling of another symbol")
        self._rows: dict[str, list[Row]] = {}
        self._mappings: dict[str, list[MappingRow]] = {}
        self._lists: dict[tuple[str, str], _ListState] = {}
        self._rejections: dict[tuple[str, str], tuple[datetime, str]] = {}
        self._scope_ids: dict[tuple[str, str], set[str]] = {}
        self._claims: list[Claim] = []
        self._series: dict[str, int] = {}
        self._groups: dict[tuple[str, int], set[str]] = {}
        self._ended: set[tuple[str, int]] = set()
        self._assets: dict[str, str] = {}
        self._log: list[dict[str, Any]] = []
        self._head = hashlib.sha256(SPEC.encode()).hexdigest()
        self._heads: dict[str, datetime] = {}
        self._symbol_iids: dict[str, set[str]] = {}
        self._token_keys: dict[str, tuple[str, int]] = {}
        self._timeline: list[tuple[datetime, str]] = []
        self._last_known_at: datetime | None = None
        self._broken = False

    # --- symbols ----------------------------------------------------------------------------------------------------------------------
    def symbol_of(self, instrument: Instrument) -> tuple[str, bool]:
        """The token symbol an instrument's base asset stands for, and whether a curated spelling was needed to get there."""
        symbol = canonical_symbol(token_symbol(instrument.base_raw, instrument.multiplier))
        alias = self._aliases.get(operator_of(instrument.venue), {}).get(symbol)
        if alias is None:
            return symbol, False
        return alias, True

    @staticmethod
    def normal_symbol(text: str) -> str:
        """A symbol from outside (an RYO answer, a post) as the registry names it: upper case. No curated spelling is applied to text from
        outside, because a code that means one token on one venue (Kraken's ``XXBT``) may mean another elsewhere."""
        return canonical_symbol(text)

    # --- admitting a list --------------------------------------------------------------------------------------------------------------
    def _next_time(self, requested: datetime) -> datetime:
        """Every record has an instant of its own: the requested time, or a millisecond after the last record if that is later."""
        moment = _floor_ms(requested)
        if self._last_known_at is not None and moment <= self._last_known_at:
            return self._last_known_at + timedelta(milliseconds=1)
        return moment

    def _usable(self) -> None:
        if self._broken:
            raise RegistryError("the registry failed to apply a record and must be rebuilt from its records")

    def plan_snapshot(self, listing: InstrumentList, *, scope: str, known_at: datetime, capture_id: str) -> dict[str, Any]:
        """The record for a venue's list: its diff against the previous list if accepted, a rejection if not. Nothing is changed."""
        self._usable()
        if scope not in SCOPES:
            raise RegistryError(f"scope {scope!r} is not one of {SCOPES}")
        if not capture_id or not listing.venue:
            raise RegistryError("a list names its venue and the capture it was read from")
        unmapped = tuple((u.raw_id, u.reason) for u in listing.unmapped)
        reason = self._rejection(listing, scope, unmapped)
        header: dict[str, Any] = {"t": "snapshot", "venue": listing.venue, "scope": scope, "capture_id": capture_id}
        events: list[dict[str, Any]] = []
        if reason is None:
            events = self._diff(listing, scope)
            header |= {"outcome": "accepted", "reason": None, "count": len(listing.instruments)}
        else:
            header |= {"outcome": "rejected", "reason": reason, "count": len(listing.instruments)}
        header["list_hash"] = _list_hash(listing.instruments, unmapped)
        header["unmapped"] = [[raw, why] for raw, why in unmapped]
        return {"spec": SPEC, "known_at": format_rfc3339_ms(self._next_time(known_at)), "events": [*events, header]}

    def admit(self, listing: InstrumentList, *, scope: str, known_at: datetime, capture_id: str) -> dict[str, Any]:
        """Plan a list, apply the record and return it: the caller appends it to the ledger."""
        record = self.plan_snapshot(listing, scope=scope, known_at=known_at, capture_id=capture_id)
        self.apply(record)
        return record

    def _rejection(self, listing: InstrumentList, scope: str, unmapped: tuple[tuple[str | None, str], ...]) -> str | None:
        instruments = listing.instruments
        if any(i.venue != listing.venue for i in instruments):
            return "VENUE_MISMATCH"
        if any(not _scope_allows(scope, i) for i in instruments):
            return "SCOPE_MISMATCH"
        if not instruments:
            return "EMPTY"
        ids = {i.instrument_id for i in instruments}
        if len(ids) != len(instruments):
            return "DUPLICATE"
        for (venue, other), owned in self._scope_ids.items():
            if venue == listing.venue and other != scope and owned & ids:
                return "SCOPE_OVERLAP"
        previous = self._lists.get((listing.venue, scope))
        if (
            previous is not None
            and previous.count >= self.config.min_list_for_shrink
            and len(instruments) < previous.count * (1 - self.config.max_shrink)
        ):
            return "SHRUNK"
        if len(unmapped) > max(self.config.unmapped_allowance, self.config.max_unmapped_share * (len(instruments) + len(unmapped))):
            return "UNMAPPED_SURGE"
        return None

    def _diff(self, listing: InstrumentList, scope: str) -> list[dict[str, Any]]:
        key = (listing.venue, scope)
        old = {iid: self._open(iid) for iid in self._scope_ids.get(key, ())}
        new = {i.instrument_id: i for i in listing.instruments}
        events: list[dict[str, Any]] = []
        for iid in sorted(new.keys() - old.keys()):
            events.append({"t": "added", "scope": scope, "instrument": instrument_record(new[iid])})
        for iid in sorted(new.keys() & old.keys()):
            row = old[iid]
            if row is not None and row.instrument != new[iid]:
                events.append({"t": "changed", "scope": scope, "instrument": instrument_record(new[iid])})
        for iid in sorted(old.keys() - new.keys()):
            events.append({"t": "removed", "scope": scope, "venue": listing.venue, "instrument_id": iid})
        return events

    def plan_claims(self, claims: Sequence[Claim], *, known_at: datetime) -> dict[str, Any]:
        """The record for new claims (sources that name the contract of a symbol)."""
        self._usable()
        if not claims:
            raise RegistryError("a claim record has at least one claim")
        events = [{"t": "claim", **claim.as_record()} for claim in claims]
        return {"spec": SPEC, "known_at": format_rfc3339_ms(self._next_time(known_at)), "events": events}

    def add_claims(self, claims: Sequence[Claim], *, known_at: datetime) -> dict[str, Any]:
        record = self.plan_claims(claims, known_at=known_at)
        self.apply(record)
        return record

    # --- applying a record ---------------------------------------------------------------------------------------------------------------
    def apply(self, record: Mapping[str, Any]) -> None:
        """Apply one record, from ``admit`` or from a replay. A registry that raises here must be thrown away and rebuilt."""
        self._usable()
        try:
            self._apply(record)
        except RegistryError:
            self._broken = True
            raise
        except (ValueError, KeyError, TypeError) as exc:
            self._broken = True
            raise RegistryError(f"a registry record is not well formed: {type(exc).__name__}: {exc}") from exc

    def _apply(self, record: Mapping[str, Any]) -> None:
        if not isinstance(record, dict) or set(record) != {"spec", "known_at", "events"} or record["spec"] != SPEC:
            raise RegistryError("not a registry record")
        known_at = parse_rfc3339(record["known_at"])
        if known_at != _floor_ms(known_at) or (self._last_known_at is not None and known_at <= self._last_known_at):
            raise RegistryError("registry records are in strictly increasing time at millisecond precision")
        events = record["events"]
        if not isinstance(events, list) or not events:
            raise RegistryError("a registry record has events")
        dirty: set[tuple[str, int]] = set()
        for event in events:
            if not isinstance(event, dict):
                raise RegistryError("an event is an object")
            kind = event["t"]
            if kind == "added":
                self._on_added(event, known_at, dirty)
            elif kind == "changed":
                self._on_changed(event, known_at, dirty)
            elif kind == "removed":
                self._on_removed(event, known_at, dirty)
            elif kind == "claim":
                self._on_claim(event, dirty)
            elif kind == "snapshot":
                self._on_snapshot(event, known_at)
            else:
                raise RegistryError(f"unknown event {kind!r}")
        self._remap(dirty, known_at)
        self._head = hashlib.sha256(self._head.encode() + b":" + canonical.dumps(dict(record))).hexdigest()
        self._heads[self._head] = known_at
        self._timeline.append((known_at, self._head))
        self._log.append(dict(record))
        self._last_known_at = known_at

    def _open(self, instrument_id: str) -> Row | None:
        rows = self._rows.get(instrument_id)
        if rows and rows[-1].known_to is None:
            return rows[-1]
        return None

    def _members(self, group: tuple[str, int]) -> list[Row]:
        """The open rows of a token series, in a fixed order."""
        return [row for iid in sorted(self._groups.get(group, ())) if (row := self._open(iid)) is not None]

    def _live(self, symbol: str, series: int) -> bool:
        return any(row.instrument.status is not InstrumentStatus.DELISTED for row in self._members((symbol, series)))

    def _series_for(self, symbol: str) -> int:
        """The series a new listing of ``symbol`` belongs to: the current one, or the next if the last one was seen to end."""
        current = self._series.get(symbol)
        if current is None:
            series = 1
        elif (symbol, current) in self._ended and not self._live(symbol, current):
            series = current + 1
        else:
            series = current
        self._series[symbol] = series
        return series

    def _enter(
        self,
        instrument: Instrument,
        scope: str,
        generation: int,
        known_at: datetime,
        dirty: set[tuple[str, int]],
        series: int | None = None,
    ) -> None:
        iid = instrument.instrument_id
        symbol, _ = self.symbol_of(instrument)
        if series is None:
            series = self._series_for(symbol)
        self._rows.setdefault(iid, []).append(Row(instrument, scope, symbol, series, generation, known_at, None))
        self._groups.setdefault((symbol, series), set()).add(iid)
        self._symbol_iids.setdefault(symbol, set()).add(iid)
        self._scope_ids.setdefault((instrument.venue, scope), set()).add(iid)
        dirty.add((symbol, series))

    def _leave(self, row: Row, known_at: datetime, dirty: set[tuple[str, int]], *, gone: bool) -> None:
        """Close a row. ``gone`` is true when the instrument leaves the list, false when it is about to be replaced by a new row."""
        iid = row.instrument.instrument_id
        self._rows[iid][-1] = Row(row.instrument, row.scope, row.symbol, row.series, row.generation, row.known_from, known_at)
        group = (row.symbol, row.series)
        self._groups[group].discard(iid)
        if gone:
            self._scope_ids[(row.instrument.venue, row.scope)].discard(iid)
            mapping = self._mappings.get(iid)
            if mapping and mapping[-1].known_to is None:
                mapping[-1] = _closed(mapping[-1], known_at)
        dirty.add(group)

    def _note_end(self, row: Row) -> None:
        """If the row that was open was the last live one of its token, the token has been seen to end: a later listing is a new series."""
        if row.instrument.status is InstrumentStatus.DELISTED:
            return  # it had already stopped being live
        group = (row.symbol, row.series)
        if not self._live(*group):
            self._ended.add(group)

    def _on_added(self, event: Mapping[str, Any], known_at: datetime, dirty: set[tuple[str, int]]) -> None:
        instrument = instrument_from_record(event["instrument"])
        scope = _scope(event)
        if not _scope_allows(scope, instrument):
            raise RegistryError("an instrument is outside the scope of its list")
        history = self._rows.get(instrument.instrument_id, [])
        if history and history[-1].known_to is None:
            raise RegistryError("an instrument is added twice")
        self._enter(instrument, scope, history[-1].generation + 1 if history else 1, known_at, dirty)

    def _on_changed(self, event: Mapping[str, Any], known_at: datetime, dirty: set[tuple[str, int]]) -> None:
        instrument = instrument_from_record(event["instrument"])
        scope = _scope(event)
        row = self._open(instrument.instrument_id)
        if row is None or row.scope != scope or row.instrument == instrument:
            raise RegistryError("a changed instrument is a listed one that differs")
        symbol, _ = self.symbol_of(instrument)
        relisted = row.instrument.status is InstrumentStatus.DELISTED and instrument.status is not InstrumentStatus.DELISTED
        moved = symbol != row.symbol or relisted
        self._leave(row, known_at, dirty, gone=False)
        if moved:
            self._enter(instrument, scope, row.generation + 1, known_at, dirty)
        else:
            self._enter(instrument, scope, row.generation, known_at, dirty, series=row.series)
        self._note_end(row)

    def _on_removed(self, event: Mapping[str, Any], known_at: datetime, dirty: set[tuple[str, int]]) -> None:
        iid = event["instrument_id"]
        row = self._open(iid) if isinstance(iid, str) else None
        if row is None or row.instrument.venue != event["venue"] or row.scope != _scope(event):
            raise RegistryError("a removed instrument is a listed one")
        self._leave(row, known_at, dirty, gone=True)
        self._note_end(row)

    def _on_claim(self, event: Mapping[str, Any], dirty: set[tuple[str, int]]) -> None:
        if set(event) != {"t", "symbol", "source", "chain_id", "contract_address"}:
            raise RegistryError("a claim event has exactly its fields")
        claim = Claim(event["symbol"], event["source"], event["chain_id"], event["contract_address"])
        if claim in self._claims:
            raise RegistryError("a claim is made twice")
        self._claims.append(claim)
        if claim.symbol in self._series:
            dirty.add((claim.symbol, self._series[claim.symbol]))

    def _on_snapshot(self, event: Mapping[str, Any], known_at: datetime) -> None:
        venue, scope = event["venue"], event["scope"]
        if event["outcome"] not in ("accepted", "rejected") or not isinstance(venue, str) or scope not in SCOPES:
            raise RegistryError("a snapshot event names its outcome, venue and scope")
        key = (venue, scope)
        unmapped = tuple((raw, why) for raw, why in event["unmapped"])
        if event["outcome"] == "rejected":
            self._rejections[key] = (known_at, str(event["reason"]))
            return
        owned = [self._open(iid) for iid in self._scope_ids.get(key, ())]
        instruments = [row.instrument for row in owned if row is not None]
        if len(instruments) != event["count"] or _list_hash(instruments, unmapped) != event["list_hash"]:
            raise RegistryError("a snapshot event does not match the instruments it was made from")
        self._lists[key] = _ListState(known_at, len(instruments), event["list_hash"], unmapped)
        self._rejections.pop(key, None)

    # --- resolving a token -------------------------------------------------------------------------------------------------------------
    def _resolve(self, symbol: str, series: int) -> Resolution:
        token_id = natural_id("tk", {"symbol": symbol, "series": series})
        rows = self._members((symbol, series))
        ids = tuple(row.instrument.instrument_id for row in rows)
        live = [row for row in rows if row.instrument.status is not InstrumentStatus.DELISTED]
        claims = [c for c in self._claims if c.symbol == symbol] if series == self._series.get(symbol, 1) else []
        operators = sorted({operator_of(row.instrument.venue) for row in live})
        evidence = (*operators, *(f"claim:{source}" for source in sorted({c.source for c in claims})))
        conflict = self._conflict(symbol, series, live, claims)
        if conflict is not None:
            return Resolution(symbol, MappingStatus.AMBIGUOUS, conflict[0], token_id, conflict[1], evidence, ids)
        if live and claims:
            return Resolution(symbol, MappingStatus.RESOLVED, Basis.CONTRACT, token_id, (), evidence, ids)
        if len(operators) >= 2:
            basis = Basis.CURATED if any(self.symbol_of(row.instrument)[1] for row in live) else Basis.VENUES
            return Resolution(symbol, MappingStatus.RESOLVED, basis, token_id, (), evidence, ids)
        basis = Basis.SINGLE_SOURCE if live else Basis.NO_LISTING
        return Resolution(symbol, MappingStatus.UNKNOWN, basis, token_id, (), evidence, ids)

    @staticmethod
    def _conflict(symbol: str, series: int, live: Sequence[Row], claims: Sequence[Claim]) -> tuple[Basis, tuple[str, ...]] | None:
        def token(**extra: str) -> str:
            return natural_id("tk", {"symbol": symbol, "series": series, **extra})

        contracts = {(c.chain_id, c.contract_address) for c in claims}
        if len(contracts) > 1:
            return Basis.CLAIM_CONFLICT, tuple(sorted(token(chain=chain, contract=address) for chain, address in contracts))
        refs: dict[str, set[str]] = {}
        for row in live:
            if row.instrument.base_ref is not None:
                refs.setdefault(row.instrument.venue, set()).add(row.instrument.base_ref)
        clashing = sorted((venue, ref) for venue, found in refs.items() if len(found) > 1 for ref in found)
        if clashing:
            return Basis.REF_CONFLICT, tuple(sorted(token(venue=venue, ref=ref) for venue, ref in clashing))
        pairs: dict[tuple[str, str, str], set[str]] = {}
        for row in live:
            if row.instrument.kind is not InstrumentKind.FUTURE:
                pairs.setdefault((row.instrument.venue, row.instrument.kind.value, row.instrument.quote_raw), set()).add(
                    row.instrument.raw_id
                )
        doubled = sorted((venue, raw_id) for (venue, _, _), found in pairs.items() if len(found) > 1 for raw_id in found)
        if doubled:
            return Basis.DUPLICATE_PAIR, tuple(sorted(token(venue=venue, pair=raw_id) for venue, raw_id in doubled))
        return None

    def _remap(self, dirty: set[tuple[str, int]], known_at: datetime) -> None:
        for symbol, series in dirty:  # each group is resolved on its own, so the order does not matter
            resolution = self._resolve(symbol, series)
            self._token_keys[resolution.token_id] = (symbol, series)
            for row in self._members((symbol, series)):
                iid = row.instrument.instrument_id
                wanted = MappingRow(
                    iid,
                    symbol,
                    series,
                    resolution.token_id,
                    resolution.status,
                    resolution.basis,
                    resolution.evidence,
                    resolution.candidates,
                    row.instrument.multiplier,
                    known_at,
                    None,
                )
                history = self._mappings.setdefault(iid, [])
                if history and history[-1].known_to is None:
                    if _same_mapping(history[-1], wanted):
                        continue
                    history[-1] = _closed(history[-1], known_at)
                history.append(wanted)
                self._assets[natural_id("as", {"token": wanted.token_id, "instrument": iid})] = iid

    # --- reading ---------------------------------------------------------------------------------------------------------------------
    @property
    def head(self) -> str:
        """The snapshot hash: it names the history of records applied so far; a decision that stores it can be joined against it."""
        return self._head

    def head_at(self, as_of: datetime) -> str:
        """The snapshot hash of the registry as it was at ``as_of``."""
        at = ensure_utc(as_of)
        found = hashlib.sha256(SPEC.encode()).hexdigest()
        for known_at, head in self._timeline:
            if known_at > at:
                break
            found = head
        return found

    def snapshot_time(self, head: str) -> datetime | None:
        """When the registry had this snapshot hash, or ``None`` if it never did."""
        return self._heads.get(head)

    def records(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._log]

    @classmethod
    def replay(
        cls,
        records: Iterable[Mapping[str, Any]],
        config: RegistryConfig | None = None,
        aliases: Mapping[str, Mapping[str, str]] | None = None,
    ) -> Registry:
        registry = cls(config, aliases)
        for record in records:
            registry.apply(record)
        return registry

    def instrument_at(self, instrument_id: str, as_of: datetime | None = None) -> Instrument | None:
        row = _visible(self._rows.get(instrument_id, []), as_of)
        return None if row is None else row.instrument

    def mapping_at(self, instrument_id: str, as_of: datetime | None = None) -> MappingRow | None:
        return _visible(self._mappings.get(instrument_id, []), as_of)

    def resolve_symbol(
        self, text: str, *, chain_id: str | None = None, contract_address: str | None = None, as_of: datetime | None = None
    ) -> Resolution:
        """What a bare symbol (an RYO answer, a post) stands for. Only a ``RESOLVED`` answer is joined; the rest are kept apart."""
        symbol = self.normal_symbol(text)
        if contract_address is None and chain_id is None:
            return self._resolve(symbol, self._series.get(symbol, 1)) if as_of is None else self._resolution_at(symbol, ensure_utc(as_of))
        if as_of is not None or contract_address is None or chain_id is None:
            raise RegistryError("a contract lookup names its chain and address and reads the registry as it is now")
        return self._for_contract(symbol, chain_id, contract_address)

    def _resolution_at(self, symbol: str, as_of: datetime) -> Resolution:
        mappings = [m for iid in sorted(self._symbol_iids.get(symbol, ())) if (m := self.mapping_at(iid, as_of)) is not None]
        if not mappings:
            return Resolution(
                symbol, MappingStatus.UNKNOWN, Basis.NO_LISTING, natural_id("tk", {"symbol": symbol, "series": 1}), (), (), ()
            )
        latest = max(m.series for m in mappings)
        chosen = [m for m in mappings if m.series == latest]
        first = chosen[0]
        return Resolution(
            symbol, first.status, first.basis, first.token_id, first.candidates, first.evidence, tuple(m.instrument_id for m in chosen)
        )

    def _for_contract(self, symbol: str, chain_id: str, contract_address: str) -> Resolution:
        address = contract_address.lower() if contract_address.startswith("0x") else contract_address
        series = self._series.get(symbol, 1)
        base = self._resolve(symbol, series)
        matching = [c for c in self._claims if c.symbol == symbol and c.chain_id == chain_id and c.contract_address == address]
        listed = any(self._open(iid) is not None for iid in base.instrument_ids)
        if not matching or not listed:
            return Resolution(
                symbol, MappingStatus.UNKNOWN, Basis.NO_LISTING if not listed else Basis.SINGLE_SOURCE, base.token_id, (), base.evidence, ()
            )
        evidence = (
            *sorted({operator_of(self._rows[iid][-1].instrument.venue) for iid in base.instrument_ids}),
            *(f"claim:{s}" for s in sorted({c.source for c in matching})),
        )
        if base.basis is Basis.CLAIM_CONFLICT:
            token = natural_id("tk", {"symbol": symbol, "series": series, "chain": chain_id, "contract": address})
            return Resolution(symbol, MappingStatus.RESOLVED, Basis.CONTRACT, token, (), evidence, ())
        if base.status is MappingStatus.AMBIGUOUS:
            return base
        return Resolution(symbol, MappingStatus.RESOLVED, Basis.CONTRACT, base.token_id, (), evidence, base.instrument_ids)

    def instruments_of(self, token_id: str, as_of: datetime | None = None) -> tuple[str, ...]:
        key = self._token_keys.get(token_id)
        if key is None:
            return ()
        at = None if as_of is None else ensure_utc(as_of)
        return tuple(
            sorted(
                iid for iid in self._symbol_iids.get(key[0], ()) if (m := self.mapping_at(iid, at)) is not None and m.token_id == token_id
            )
        )

    def list_age(self, venue: str, scope: str, now: datetime) -> timedelta | None:
        """How old the current list of a venue and scope is: the time since the last accepted one. ``None`` if none was ever accepted."""
        state = self._lists.get((venue, scope))
        return None if state is None else ensure_utc(now) - state.accepted_at

    def last_rejection(self, venue: str, scope: str) -> tuple[datetime, str] | None:
        return self._rejections.get((venue, scope))

    def unmapped(self, venue: str, scope: str) -> tuple[tuple[str | None, str], ...]:
        """The entries the venue listed that the adapter could not map, with the reason: unmapped, not absent."""
        state = self._lists.get((venue, scope))
        return () if state is None else state.unmapped

    def status_counts(self, as_of: datetime | None = None) -> dict[str, int]:
        counts = {status.value: 0 for status in MappingStatus}
        for iid in self._rows:
            mapping = self.mapping_at(iid, as_of)
            if mapping is not None:
                counts[mapping.status.value] += 1
        return counts

    # --- joining ---------------------------------------------------------------------------------------------------------------------
    def lookup_asset(self, asset_id: str, as_of: datetime | None = None) -> Asset | None:
        """The one ``(token, instrument)`` an asset id names, if it is still a resolved mapping at ``as_of``."""
        iid = self._assets.get(asset_id)
        if iid is None:
            return None
        result = self.join(iid, Want(), as_of)
        if result.asset is None or result.asset.asset_id != asset_id:
            return None
        return result.asset

    def join(self, instrument_id: str, want: Want, as_of: datetime | None = None) -> JoinResult:
        """May evidence measured as ``want`` be joined to this instrument? Anything but ``OK`` names the field that does not match."""
        at = None if as_of is None else ensure_utc(as_of)
        row = _visible(self._rows.get(instrument_id, []), at)
        mapping = self.mapping_at(instrument_id, at)
        if row is None or mapping is None:
            return JoinResult(JoinCode.UNKNOWN_INSTRUMENT, "instrument", "the registry does not list this instrument", None)
        if want.snapshot_hash is not None:
            then = self._heads.get(want.snapshot_hash)
            if then is None:
                return JoinResult(JoinCode.SNAPSHOT_MISMATCH, "snapshot_hash", "the registry never had that snapshot", None)
            before = _visible(self._rows.get(instrument_id, []), then)
            earlier = self.mapping_at(instrument_id, then)
            if before is None or earlier is None:
                return JoinResult(JoinCode.SNAPSHOT_MISMATCH, "snapshot_hash", "the instrument was not in that snapshot", None)
            same = (before.instrument.base_raw, before.instrument.base_ref, before.instrument.quote_raw, before.instrument.multiplier) == (
                row.instrument.base_raw,
                row.instrument.base_ref,
                row.instrument.quote_raw,
                row.instrument.multiplier,
            )
            if not same or earlier.token_id != mapping.token_id:
                return JoinResult(
                    JoinCode.SNAPSHOT_MISMATCH, "snapshot_hash", "the instrument means something else than it did in that snapshot", None
                )
        if mapping.status is MappingStatus.AMBIGUOUS:
            return JoinResult(
                JoinCode.IDENTITY_AMBIGUOUS,
                "token",
                f"the symbol {mapping.symbol} is claimed by more than one token ({mapping.basis.value})",
                None,
            )
        if mapping.status is MappingStatus.UNKNOWN:
            return JoinResult(JoinCode.IDENTITY_UNRESOLVED, "token", f"only one source stands behind {mapping.symbol}", None)
        instrument = row.instrument
        for name, wanted, have in (
            ("token", want.token_id, mapping.token_id),
            ("kind", None if want.kind is None else want.kind.value, instrument.kind.value),
            ("quote_class", want.quote_class, instrument.quote_class),
            ("multiplier", want.multiplier, instrument.multiplier),
        ):
            if wanted is not None and wanted != have:
                return JoinResult(JoinCode.IDENTITY_MISMATCH, name, f"the evidence has {name} {wanted!r}, the instrument {have!r}", None)
        asset = Asset(
            natural_id("as", {"token": mapping.token_id, "instrument": instrument_id}),
            mapping.token_id,
            instrument_id,
            instrument.venue,
            instrument.raw_id,
            instrument.kind,
            instrument.base_raw,
            instrument.quote_raw,
            instrument.quote_class,
            instrument.multiplier,
            instrument.settle,
            self._head if at is None else self.head_at(at),
        )
        return JoinResult(JoinCode.OK, None, "", asset)


def _scope(event: Mapping[str, Any]) -> str:
    scope = event["scope"]
    if scope not in SCOPES:
        raise RegistryError("an event names a scope that exists")
    return str(scope)


def _closed(mapping: MappingRow, known_at: datetime) -> MappingRow:
    return MappingRow(
        mapping.instrument_id,
        mapping.symbol,
        mapping.series,
        mapping.token_id,
        mapping.status,
        mapping.basis,
        mapping.evidence,
        mapping.candidates,
        mapping.multiplier,
        mapping.known_from,
        known_at,
    )


def _same_mapping(a: MappingRow, b: MappingRow) -> bool:
    return (a.symbol, a.series, a.token_id, a.status, a.basis, a.evidence, a.candidates, a.multiplier) == (
        b.symbol,
        b.series,
        b.token_id,
        b.status,
        b.basis,
        b.evidence,
        b.candidates,
        b.multiplier,
    )


def _visible[T: (Row, MappingRow)](rows: Sequence[T], as_of: datetime | None) -> T | None:
    """The row that is current, or the one that was current at ``as_of``."""
    for row in reversed(rows):
        if as_of is None:
            return row if row.known_to is None else None
        if row.known_from <= as_of and (row.known_to is None or as_of < row.known_to):
            return row
    return None


__all__ = [
    "Asset",
    "Basis",
    "Claim",
    "JoinCode",
    "JoinResult",
    "MappingRow",
    "MappingStatus",
    "Registry",
    "RegistryConfig",
    "RegistryError",
    "Resolution",
    "Row",
    "Want",
    "canonical_symbol",
    "instrument_from_record",
    "instrument_record",
    "operator_of",
]
