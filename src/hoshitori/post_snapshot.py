"""Events that arrive after a cycle's snapshot, and the revisions they cause (contract sections 25 item 11, 33 and 52).

An event is a change the registry saw after a cycle sealed its
snapshot. It is a record of its own: it never enters an evidence store an arm reads, never edits a thesis and never changes what a
decision rested on. What it can do is cause a *revision*: a linked record that says whether a thesis's premise changed. Nothing here
guesses.

* **Identity, not tickers.** An event reaches a thesis only when the event's canonical asset equals the thesis's asset and the event's
  venue is one of the thesis's oracle venues. Two events about one token touch only the theses whose asset and oracle they concern.
* **Materiality is re-evaluated, deterministically.** A thesis settles on its oracle instruments. A removal, or a status that is not
  trading, on an oracle instrument of the thesis changes the premise ``ORACLE_INSTRUMENT_AVAILABLE``: ``MATERIAL_UPDATE``. A new
  instrument, trading resumed, a duplicate or an event on a venue the thesis does not use is ``NO_MATERIAL_CHANGE`` with its reason. A
  headline with no premise effect never becomes a changed conclusion.
* **Duplicates and corrections are relations, not rewrites.** The same change reported again is ``DUPLICATE_OF`` the first and causes
  no revision; a correction names what it corrects and the original stays.
* **Written together.** An event and its revisions are acknowledged in one step, so a kill leaves all of them or none, and the same
  input again finds what was written.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from hoshitori import canonical, envelope, triggers
from hoshitori.evidence import record_digest
from hoshitori.ids import natural_id
from hoshitori.league import put
from hoshitori.ledger import Ledger
from hoshitori.registry import Registry, Want
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339

EVENT_TYPE: Final = "post_snapshot_event"
REVISION_TYPE: Final = "thesis_revision"
TYPE_VERSION: Final = 1
KINDS: Final = ("REGISTRY_NEW_INSTRUMENT", "REGISTRY_INSTRUMENT_REMOVED", "REGISTRY_STATUS_CHANGED")
STATUSES: Final = ("TRADING", "HALTED", "DELISTED", "UNKNOWN")
RELATIONS: Final = ("NEW", "DUPLICATE_OF", "CORRECTS")
MATERIAL: Final = "MATERIAL_UPDATE"
NOT_MATERIAL: Final = "NO_MATERIAL_CHANGE"
PREMISE: Final = "ORACLE_INSTRUMENT_AVAILABLE"
REASONS: Final = ("ORACLE_IMPAIRED", "INSTRUMENT_ADDED", "TRADING_RESUMED")
MAX_INSTANT_MS: Final = 253_402_300_799_999
_IDENT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HASH: Final = re.compile(r"[0-9a-f]{64}")


class PostSnapshotError(ValueError):
    """An event, a revision or a record that does not satisfy the rules."""


@dataclass(frozen=True)
class Event:
    kind: str
    asset_id: str
    instrument_id: str
    venue: str
    status: str | None  # the status a status change moved to; none for the other kinds
    first_seen_ms: int
    ingested_ms: int
    previous_snapshot: tuple[str, str] | None  # (snapshot id, its sha-256): the latest sealed snapshot when the event was first seen
    relation: str
    related: str | None  # the event a duplicate repeats or a correction corrects
    captures: tuple[tuple[str, str], ...]  # (capture id, sha-256) of the registry records the event rests on

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise PostSnapshotError(f"an event is one of {KINDS}")
        if (self.kind == "REGISTRY_STATUS_CHANGED") != (self.status is not None) or (
            self.status is not None and self.status not in STATUSES
        ):
            raise PostSnapshotError("a status change names the status it moved to, from the closed list; the other kinds name none")
        for value, what in ((self.asset_id, "the asset"), (self.instrument_id, "the instrument"), (self.venue, "the venue")):
            if not isinstance(value, str) or _IDENT.fullmatch(value) is None:
                raise PostSnapshotError(f"{what} is an identifier, never free text")
        for instant, label in ((self.first_seen_ms, "the first-seen time"), (self.ingested_ms, "the ingestion time")):
            if isinstance(instant, bool) or not isinstance(instant, int) or not 0 <= instant <= MAX_INSTANT_MS:
                raise PostSnapshotError(f"{label} is a whole number of milliseconds")
        if self.first_seen_ms > self.ingested_ms:
            raise PostSnapshotError("an event cannot be first seen after we ingested it")
        if self.relation not in RELATIONS or (self.relation == "NEW") != (self.related is None):
            raise PostSnapshotError("a new event names no other event; a duplicate or a correction names one")
        if self.related is not None and _IDENT.fullmatch(self.related) is None:
            raise PostSnapshotError("the related event is an event id")
        if self.previous_snapshot is not None:
            ident, digest = self.previous_snapshot
            if _IDENT.fullmatch(ident) is None or _HASH.fullmatch(digest) is None:
                raise PostSnapshotError("the previous snapshot is an id and a digest")
        for ident, digest in self.captures:
            if _IDENT.fullmatch(ident) is None or _HASH.fullmatch(digest) is None:
                raise PostSnapshotError("a capture is an id and a digest")

    @property
    def dedupe_key(self) -> str:
        return natural_id(
            "dk", {"kind": self.kind, "asset": self.asset_id, "instrument": self.instrument_id, "venue": self.venue, "status": self.status}
        )

    @property
    def event_id(self) -> str:
        return natural_id("pe", {"dedupe": self.dedupe_key, "seen": self.first_seen_ms, "relation": self.relation, "related": self.related})


@dataclass(frozen=True)
class Thesis:
    """The parts of a forecast record an event is matched against."""

    thesis_id: str
    digest: str
    asset_id: str
    oracle_venues: tuple[str, ...]
    as_of_ms: int  # when the evidence the thesis rested on was taken: an event reaches it only if it was first seen after this


@dataclass(frozen=True)
class Revision:
    event_id: str
    thesis_id: str
    thesis_digest: str
    kind: str
    changed: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.kind not in (MATERIAL, NOT_MATERIAL) or self.reason not in REASONS:
            raise PostSnapshotError("a revision is material or not, with a reason from the closed list")
        if (self.kind == MATERIAL) != bool(self.changed) or any(c != PREMISE for c in self.changed):
            raise PostSnapshotError("a material revision names the premise that changed; the other names none")

    @property
    def revision_id(self) -> str:
        return natural_id("tr", {"event": self.event_id, "thesis": self.thesis_id})


def affects(event: Event, thesis: Thesis) -> bool:
    """Canonical identity only: the same asset, on a venue the thesis settles on, and first seen after the thesis's evidence was taken."""
    return event.asset_id == thesis.asset_id and event.venue in thesis.oracle_venues and thesis.as_of_ms < event.first_seen_ms


def evaluate(event: Event, thesis: Thesis) -> Revision:
    """Whether the event changes a premise of the thesis."""
    impaired = event.kind == "REGISTRY_INSTRUMENT_REMOVED" or (event.kind == "REGISTRY_STATUS_CHANGED" and event.status != "TRADING")
    if impaired:
        return Revision(event.event_id, thesis.thesis_id, thesis.digest, MATERIAL, (PREMISE,), "ORACLE_IMPAIRED")
    reason = "INSTRUMENT_ADDED" if event.kind == "REGISTRY_NEW_INSTRUMENT" else "TRADING_RESUMED"
    return Revision(event.event_id, thesis.thesis_id, thesis.digest, NOT_MATERIAL, (), reason)


def event_record(event: Event, source: str) -> dict[str, canonical.JsonValue]:
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": EVENT_TYPE,
        "type_version": TYPE_VERSION,
        "id": event.event_id,
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(event.ingested_ms)),
        "body": {
            "kind": event.kind,
            "asset_id": event.asset_id,
            "instrument_id": event.instrument_id,
            "venue": event.venue,
            "status": event.status,
            "first_seen_at": format_rfc3339_ms(from_ms(event.first_seen_ms)),
            "previous_snapshot": None if event.previous_snapshot is None else list(event.previous_snapshot),
            "dedupe_key": event.dedupe_key,
            "relation": event.relation,
            "related": event.related,
            "captures": [list(pair) for pair in event.captures],
        },
        "privacy": "public",
    }
    _check(record)
    return record


def revision_record(revision: Revision, source: str, created_ms: int, event_digest: str) -> dict[str, canonical.JsonValue]:
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": REVISION_TYPE,
        "type_version": TYPE_VERSION,
        "id": revision.revision_id,
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_ms)),
        "body": {
            "event_id": revision.event_id,
            "thesis_id": revision.thesis_id,
            "thesis_digest": revision.thesis_digest,
            "kind": revision.kind,
            "changed": list(revision.changed),
            "reason": revision.reason,
        },
        "refs": [{"id": revision.thesis_id, "sha256": revision.thesis_digest}, {"id": revision.event_id, "sha256": event_digest}],
        "privacy": "public",
    }
    _check(record)
    return record


def _check(record: dict[str, canonical.JsonValue]) -> None:
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise PostSnapshotError(f"the record does not satisfy the envelope: {exc}") from exc


def theses_of(records: Sequence[Mapping[str, Any]]) -> tuple[Thesis, ...]:
    """The forecasts of a ledger that are still open, as the theses an event is matched against: their own id and digest, canonical asset,
    oracle venues and the time their evidence was taken. A forecast that has a settlement is over, and a premise changing afterwards
    revises nothing."""
    settled = {str(r["body"]["forecast_id"]) for r in records if r.get("type") == "settlement"}
    found = []
    for record in records:
        if record.get("type") != "forecast" or record["id"] in settled:
            continue
        body = record["body"]
        clean = {k: v for k, v in record.items() if k != "committed_at"}
        as_of = to_ms(parse_rfc3339(body["as_of"]))
        found.append(
            Thesis(str(record["id"]), record_digest(clean), body["asset_id"], (body["oracle_primary"], body["oracle_second"]), as_of)
        )
    return tuple(found)


def events_of(
    registry: Registry,
    *,
    after_ms: int,
    until_ms: int,
    previous_snapshot: tuple[str, str] | None,
    ingested_ms: int,
    captures: tuple[tuple[str, str], ...] = (),
) -> tuple[tuple[Event, ...], int]:
    """The registry's changes in ``(after_ms, until_ms]`` as events, and how many could not be given a canonical asset. A change to an
    instrument the registry cannot join to exactly one asset (an ambiguous ticker) has no identity to match a thesis by: it is counted,
    never guessed."""
    found: list[Event] = []
    unresolved = 0
    for change in triggers.registry_changes(registry, after_ms=after_ms, until_ms=until_ms):
        before = 1 if change.rule == triggers.REMOVED else 0  # a removed instrument is joined as it stood just before the list lost it
        joined = registry.join(change.instrument.instrument_id, Want(), from_ms(change.known_at_ms - before))
        if not joined.ok or joined.asset is None:
            unresolved += 1
            continue
        status = change.instrument.status.value if change.rule == triggers.STATUS else None
        found.append(
            Event(
                change.rule,
                joined.asset.asset_id,
                change.instrument.instrument_id,
                change.venue,
                status,
                change.known_at_ms,
                max(ingested_ms, change.known_at_ms),
                previous_snapshot,
                "NEW",
                None,
                captures,
            )
        )
    return tuple(found), unresolved


def _existing(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The events already in the ledger, by dedupe key, the first one seen for each key."""
    found: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if record.get("type") == EVENT_TYPE and record["body"]["relation"] == "NEW":
            found.setdefault(record["body"]["dedupe_key"], record)
    return found


def _revision_of(body: Mapping[str, Any]) -> Revision:
    return Revision(body["event_id"], body["thesis_id"], body["thesis_digest"], body["kind"], tuple(body["changed"]), body["reason"])


def ingest(ledger: Ledger, event: Event, theses: Sequence[Thesis], *, source: str) -> tuple[Event, tuple[Revision, ...]]:
    """Record the event (as a duplicate of an earlier report of the same change, when there is one) and a revision for every thesis it
    reaches. The same event again writes nothing and returns what was written."""
    records = [cast(dict[str, Any], canonical.parse_record(ledger.read_record(seq))) for seq in range(ledger.size)]
    first = _existing(records).get(event.dedupe_key)
    if event.relation == "NEW" and first is not None and first["id"] != event.event_id:
        event = Event(
            event.kind,
            event.asset_id,
            event.instrument_id,
            event.venue,
            event.status,
            event.first_seen_ms,
            event.ingested_ms,
            event.previous_snapshot,
            "DUPLICATE_OF",
            str(first["id"]),
            event.captures,
        )
    if any(r["id"] == event.event_id and r.get("type") == EVENT_TYPE for r in records):
        written = tuple(
            _revision_of(r["body"]) for r in records if r.get("type") == REVISION_TYPE and r["body"]["event_id"] == event.event_id
        )
        return event, written
    revisions = () if event.relation == "DUPLICATE_OF" else tuple(evaluate(event, t) for t in theses if affects(event, t))
    sealed = event_record(event, source)
    digest = record_digest(sealed)
    batch = [sealed, *(revision_record(r, source, event.ingested_ms, digest) for r in revisions)]
    put(ledger, batch)
    return event, revisions
