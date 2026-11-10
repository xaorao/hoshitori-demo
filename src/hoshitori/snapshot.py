"""The frozen snapshot of a cycle: the evidence every arm reads, fixed before any arm starts, and the reader that gives an arm
nothing more.

Contract section 25 and ``LEAGUE_EXPERIMENT`` section 4 are the rules. The collector gathers evidence for the manifest's
candidates only, through the gateway, and hands the ids it collected to ``freeze``. What freezing does:

* **Only what was collected, only for the manifest.** Items are named by id, for one candidate or for the cycle as a whole. A
  candidate outside the manifest, an item listed twice, an item the store does not hold and an item about another candidate
  are refused: the collector made a mistake, and a snapshot is never built round a mistake.
* **Nothing from the future, nothing revised away.** An item known after ``snapshot_at`` is refused, and so is an item that
  has a newer revision known by then (the collector lists the revision).
* **No interpretation reaches an independent arm.** An observation or a derived item that stands on an interpretation
  anywhere in its lineage is refused, and so is an RYO observation that carries a field the evidence-class table calls an
  interpretation (or does not know). Interpretations may be held (arm R's record needs them) but the ``INDEPENDENT`` reader
  never returns one.
* **Stale is left out and said.** Each origin family has a limit on how old an item may be when it was collected (price 60 s,
  RYO 300 s, news 900 s, the contract's priors, section 16): its age is the time from what the source says it describes to when
  it was received, never less than nothing. And no item is held longer than a cycle may take from discovery to snapshot
  (``DISCOVERY_TO_SNAPSHOT_MS``): one received earlier belongs to no collection of this cycle. A slow source (RYO answers in a
  minute or two) therefore loses nothing it delivered fresh, while how old each item is when the forecast starts is recorded
  beside the forecast. An item that fails either is not admitted and is listed with its reason, as is an item whose parent was
  not admitted. A required source with no admitted item leaves that candidate ``missing`` it: the arms
  then end that slot in ``FAILED_EVIDENCE``, never in a call and never in an abstention.
* **Frozen means a copy.** The snapshot holds the items themselves and the digest of each record, so nothing written to the
  store later (a revision, a new reading) can change what an arm sees. Its hash covers the cycle, the manifest hash, the
  time, the freshness limits, the required sources and every admitted and rejected item, so two snapshots with one hash are
  the same snapshot.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from hoshitori import canonical, envelope
from hoshitori.evidence import EvidenceError, EvidenceItem, FamilyKind, Kind, record_digest
from hoshitori.evidence import from_record as evidence_from_record
from hoshitori.evidence_sources import pattern_of
from hoshitori.ids import natural_id
from hoshitori.lineage import EvidenceStore, LineageError, observation_set_hash
from hoshitori.manifest import Manifest
from hoshitori.ryo_fields import SPECS, classification_table
from hoshitori.timeline import DISCOVERY_TO_SNAPSHOT_MS, from_ms, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339

TYPE: Final = "snapshot"
TYPE_VERSION: Final = 1
MAX_INSTANT_MS: Final = 253_402_300_799_999
MAX_LIMIT_MS: Final = 7 * 24 * 3600 * 1000
_BODY: Final = frozenset(
    {"cycle_id", "manifest_hash", "snapshot_at", "freshness", "required", "shared", "shared_rejected", "candidates", "sha256"}
)
_CANDIDATE: Final = frozenset({"candidate_id", "asset_id", "items", "missing", "rejected"})
DEFAULT_FRESHNESS: Final[Mapping[FamilyKind, int]] = {FamilyKind.EXCHANGE: 60_000, FamilyKind.RYO: 300_000, FamilyKind.NEWS: 900_000}


class SnapshotError(ValueError):
    """A snapshot that cannot be frozen, or a reader asked for something outside what it may give."""


class Access(StrEnum):
    INDEPENDENT = "INDEPENDENT"  # observations and what is derived from them: the arms of the league
    EVERYTHING = "EVERYTHING"  # also interpretations: the record of RYO's own answer and audits


class Reject(StrEnum):
    STALE = "STALE"
    PARENT_NOT_ADMITTED = "PARENT_NOT_ADMITTED"


_VISIBLE: Final = {Access.INDEPENDENT: frozenset({Kind.OBSERVATION, Kind.DERIVED}), Access.EVERYTHING: frozenset(Kind)}


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    asset_id: str


@dataclass(frozen=True)
class Rejection:
    evidence_id: str
    reason: Reject


UNAVAILABLE_REASONS: Final = (
    "NO_ASSET",
    "NO_ADAPTER",
    "NO_SERVER_TIME",
    "BAD_REQUEST",
    "CALL_FAILED",
    "UNREADABLE",
    "TIMED_OUT",
    "NO_PERPETUAL",
    "PERPETUAL_LIST_UNUSABLE",
)  # the collector's closed reasons (a check holds the two lists equal); free text from a venue never reaches a record


@dataclass(frozen=True)
class Unavailable:
    """A source the collector asked for a candidate and could not read: which source, why (a closed word), and the window that failed."""

    source: str
    reason: str
    interval_s: int | None


@dataclass(frozen=True)
class Slot:
    candidate_id: str
    asset_id: str
    item_ids: tuple[str, ...]
    missing: tuple[str, ...]
    rejected: tuple[Rejection, ...]
    unavailable: tuple[Unavailable, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    cycle_id: str
    manifest_hash: str
    snapshot_at_ms: int
    freshness: tuple[tuple[str, int], ...]
    required: tuple[str, ...]
    shared: tuple[str, ...]
    shared_rejected: tuple[Rejection, ...]
    slots: tuple[Slot, ...]
    items: tuple[tuple[EvidenceItem, str], ...]  # (item, digest of its record), by evidence id

    def body(self) -> dict[str, canonical.JsonValue]:
        digests = {item.evidence_id: digest for item, digest in self.items}

        def refs(ids: Sequence[str]) -> list[canonical.JsonValue]:
            return [{"evidence_id": ident, "sha256": digests[ident]} for ident in ids]

        def rejections(found: Sequence[Rejection]) -> list[canonical.JsonValue]:
            return [{"evidence_id": r.evidence_id, "reason": r.reason.value} for r in found]

        def candidate(slot: Slot) -> dict[str, canonical.JsonValue]:
            entry: dict[str, canonical.JsonValue] = {
                "candidate_id": slot.candidate_id,
                "asset_id": slot.asset_id,
                "items": refs(slot.item_ids),
                "missing": list(slot.missing),
                "rejected": rejections(slot.rejected),
            }
            if slot.unavailable:  # present only when something could not be read, so a snapshot without one has the hash it always had
                entry["unavailable"] = [{"source": u.source, "reason": u.reason, "interval_s": u.interval_s} for u in slot.unavailable]
            return entry

        return {
            "cycle_id": self.cycle_id,
            "manifest_hash": self.manifest_hash,
            "snapshot_at": format_rfc3339_ms(from_ms(self.snapshot_at_ms)),
            "freshness": dict(self.freshness),
            "required": list(self.required),
            "shared": refs(self.shared),
            "shared_rejected": rejections(self.shared_rejected),
            "candidates": [candidate(slot) for slot in self.slots],
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical.dumps(self.body())).hexdigest()


def candidates_of(manifest: Manifest) -> tuple[Candidate, ...]:
    """The manifest's eligible union as the candidates a snapshot is collected for."""
    return tuple(Candidate(entry.candidate_id, str(entry.asset_id)) for entry in manifest.union())


# --- freezing ---------------------------------------------------------------------------------------------------------------------------
def _text(value: object, what: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise SnapshotError(f"{what} is a non-empty string of at most 200 characters")
    try:
        canonical.dumps({"v": value})
    except canonical.CanonicalError as exc:
        raise SnapshotError(f"{what} is not valid canonical text: {exc}") from exc


def _check_inputs(
    cycle_id: str,
    manifest_hash: str,
    snapshot_at_ms: int,
    candidates: Sequence[Candidate],
    required: Sequence[str],
    freshness: Mapping[FamilyKind, int],
) -> None:
    _text(cycle_id, "the cycle id")
    if not isinstance(manifest_hash, str) or len(manifest_hash) != 64 or any(ch not in "0123456789abcdef" for ch in manifest_hash):
        raise SnapshotError("the manifest hash is 64 lower-case hexadecimal characters")
    if isinstance(snapshot_at_ms, bool) or not isinstance(snapshot_at_ms, int) or not 0 <= snapshot_at_ms <= MAX_INSTANT_MS:
        raise SnapshotError("snapshot_at is a whole number of milliseconds up to the end of the year 9999")
    for candidate in candidates:
        _text(candidate.candidate_id, "a candidate id")
        _text(candidate.asset_id, "a candidate's asset id")
    if len({c.candidate_id for c in candidates}) != len(candidates) or len({c.asset_id for c in candidates}) != len(candidates):
        raise SnapshotError("candidates are distinct, and so are their assets")
    for source in required:
        _text(source, "a required source")
    if len(set(required)) != len(required):
        raise SnapshotError("required sources are distinct")
    for kind, limit in freshness.items():
        if not isinstance(kind, FamilyKind) or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT_MS:
            raise SnapshotError("a freshness limit is a whole number of milliseconds from 1 to 7 days, for a family kind")


def _stands_on_interpretation(store: EvidenceStore, item: EvidenceItem) -> bool:
    return any(store.get(ancestor).kind is Kind.INTERPRETATION for ancestor in store.ancestors(item.evidence_id))


def _unclassified_field(item: EvidenceItem) -> bool:
    """An RYO observation with a field that the evidence-class table does not call an observation (an unknown field counts)."""
    classes = {row[0]: row[1] for row in classification_table(str(item.tool))}
    return any(classes.get(pattern_of(path)) != "OBSERVATION" for path, _ in item.fields)


def _leaks_interpretation(store: EvidenceStore, item: EvidenceItem) -> bool:
    return item.kind is not Kind.INTERPRETATION and (
        _stands_on_interpretation(store, item) or (item.kind is Kind.OBSERVATION and item.tool in SPECS and _unclassified_field(item))
    )


def _about(item: EvidenceItem, asset_id: str | None, others: frozenset[str]) -> bool:
    """Whether an item belongs where it was listed: nothing about an asset for the whole cycle, and for a candidate only its own."""
    assets = {ref[0] for ref in item.asset_refs}
    if asset_id is None:
        return not assets
    return asset_id in {ref[0] for ref in item.asset_refs if ref[2] == "RESOLVED"} and not assets & others


def missing_sources(required: Sequence[str], items: Iterable[EvidenceItem]) -> tuple[str, ...]:
    """The required sources that none of a candidate's admitted items comes from; an interpretation does not count. The one rule for a
    frozen snapshot and for a what-if snapshot with something taken out (``intervention``)."""
    seen = {item.source for item in items if item.kind is not Kind.INTERPRETATION}
    return tuple(source for source in required if source not in seen)


def _unavailable_by_candidate(
    given: Mapping[str, Sequence[Unavailable]], known: Mapping[str, Candidate]
) -> dict[str, tuple[Unavailable, ...]]:
    """What was not read, checked and put in a fixed order; a malformed entry is refused, never repaired."""
    out: dict[str, tuple[Unavailable, ...]] = {}
    for cid, entries in given.items():
        if cid not in known:
            raise SnapshotError("something was reported unread for a candidate outside the manifest")
        for u in entries:
            if not isinstance(u.source, str) or not u.source or u.source != u.source.strip():
                raise SnapshotError("an unread source has a name")
            if u.reason not in UNAVAILABLE_REASONS:
                raise SnapshotError(f"an unread source has a reason from {list(UNAVAILABLE_REASONS)}")
            if u.interval_s is not None and (isinstance(u.interval_s, bool) or not isinstance(u.interval_s, int) or u.interval_s <= 0):
                raise SnapshotError("the window of an unread source is a positive number of seconds, or none")
        ordered = tuple(sorted(entries, key=lambda u: (u.source, u.reason, u.interval_s or 0)))
        if len(set(ordered)) != len(ordered):
            raise SnapshotError("the same unread source is listed twice")
        if ordered:
            out[cid] = ordered
    return out


def freeze(
    store: EvidenceStore,
    *,
    cycle_id: str,
    manifest_hash: str,
    snapshot_at_ms: int,
    candidates: Sequence[Candidate],
    shared: Sequence[str],
    by_candidate: Mapping[str, Sequence[str]],
    required: Sequence[str],
    freshness: Mapping[FamilyKind, int] = DEFAULT_FRESHNESS,
    unavailable: Mapping[str, Sequence[Unavailable]] | None = None,
) -> Snapshot:
    """Fix the snapshot of a cycle from the items the collector named, or refuse."""
    _check_inputs(cycle_id, manifest_hash, snapshot_at_ms, candidates, required, freshness)
    known = {c.candidate_id: c for c in candidates}
    unread = _unavailable_by_candidate(unavailable or {}, known)
    if not set(by_candidate) <= set(known):
        raise SnapshotError("evidence was collected for a candidate outside the manifest")
    listed: dict[str, str | None] = {}  # evidence id -> candidate id, or None for the whole cycle
    for owner, ids in [(None, shared), *((cid, by_candidate[cid]) for cid in sorted(by_candidate))]:
        for ident in ids:
            if ident in listed:
                raise SnapshotError(f"{ident} is listed twice")
            listed[ident] = owner
    others = {cid: frozenset(c.asset_id for k, c in known.items() if k != cid) for cid in known}
    items: dict[str, EvidenceItem] = {}
    for ident, owner in listed.items():
        try:
            item = store.get(ident)
            newest = store.latest(ident, snapshot_at_ms)
        except LineageError as exc:
            raise SnapshotError(f"the store does not hold {ident}") from exc
        if item.known_at_ms > snapshot_at_ms:
            raise SnapshotError(f"{ident} was known after the snapshot")
        if newest.evidence_id != ident:
            raise SnapshotError(f"{ident} has a newer revision known by the snapshot; list the revision")
        if not _about(item, None if owner is None else known[owner].asset_id, frozenset() if owner is None else others[owner]):
            raise SnapshotError(f"{ident} is not about what it was listed for")
        if _leaks_interpretation(store, item):
            raise SnapshotError(f"{ident} brings something other than an observation to the independent arms")
        items[ident] = item
        for parent, _ in item.derived_from:
            if parent in listed and listed[parent] not in (None, owner):
                raise SnapshotError(f"{ident} is derived from an item of another candidate")
    stale = {
        ident
        for ident, item in items.items()
        if snapshot_at_ms - item.received_at_ms > DISCOVERY_TO_SNAPSHOT_MS
        or (
            (limit := freshness.get(item.origin.kind)) is not None
            and item.received_at_ms
            - min(item.event_time_ms if item.event_time_ms is not None else item.received_at_ms, item.received_at_ms)
            > limit
        )
    }
    verdict: dict[str, bool] = {}

    def admit(ident: str) -> bool:
        """Whether an item is admitted: fresh, and standing only on parents that are admitted (and listed)."""
        if ident not in verdict:
            verdict[ident] = ident in items and ident not in stale and all(admit(parent) for parent, _ in items[ident].derived_from)
        return verdict[ident]

    rejected = {ident: (Reject.STALE if ident in stale else Reject.PARENT_NOT_ADMITTED) for ident in items if not admit(ident)}
    slots: list[Slot] = []
    for cid in sorted(known):
        mine = sorted(i for i, owner in listed.items() if owner == cid)
        admitted = [i for i in mine if i not in rejected]
        missing = missing_sources(required, (items[i] for i in admitted))
        slots.append(
            Slot(
                cid,
                known[cid].asset_id,
                tuple(admitted),
                missing,
                tuple(Rejection(i, rejected[i]) for i in mine if i in rejected),
                unread.get(cid, ()),
            )
        )
    everyone = sorted(i for i, owner in listed.items() if owner is None)
    kept = sorted(i for i in listed if i not in rejected)
    held = tuple((items[i], store.digest(i)) for i in kept)
    return Snapshot(
        cycle_id,
        manifest_hash,
        snapshot_at_ms,
        tuple(sorted((kind.value, limit) for kind, limit in freshness.items())),
        tuple(required),
        tuple(i for i in everyone if i not in rejected),
        tuple(Rejection(i, rejected[i]) for i in everyone if i in rejected),
        tuple(slots),
        held,
    )


# --- reading ----------------------------------------------------------------------------------------------------------------------------
class Reader:
    """What one kind of arm may read from a snapshot: its items, its hash, and nothing else."""

    def __init__(self, snapshot: Snapshot, access: Access) -> None:
        if not isinstance(access, Access):
            raise SnapshotError("an access is one of the listed kinds")
        self._snapshot = snapshot
        self.access = access
        self._items = {item.evidence_id: (item, digest) for item, digest in snapshot.items}
        self._slots = {slot.candidate_id: slot for slot in snapshot.slots}

    def _slot(self, candidate_id: str) -> Slot:
        try:
            return self._slots[candidate_id]
        except KeyError:
            raise SnapshotError(f"{candidate_id} is not a candidate of this snapshot") from None

    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(self._slots)

    def ids(self, candidate_id: str) -> tuple[str, ...]:
        """The items of the whole cycle and of this candidate that this access may read, by id."""
        wanted = _VISIBLE[self.access]
        return tuple(i for i in sorted({*self._snapshot.shared, *self._slot(candidate_id).item_ids}) if self._items[i][0].kind in wanted)

    def items(self, candidate_id: str) -> tuple[EvidenceItem, ...]:
        return tuple(self._items[i][0] for i in self.ids(candidate_id))

    def get(self, evidence_id: str, candidate_id: str) -> EvidenceItem:
        if evidence_id not in self.ids(candidate_id):
            raise SnapshotError(f"{evidence_id} is not in what this reader may read for {candidate_id}")
        return self._items[evidence_id][0]

    def missing(self, candidate_id: str) -> tuple[str, ...]:
        return self._slot(candidate_id).missing

    def complete(self, candidate_id: str) -> bool:
        return not self.missing(candidate_id)

    def set_hash(self, candidate_id: str, used: Sequence[str] | None = None) -> str:
        """The observation-set hash of what this reader may read for a candidate, or of the part of it that a decision used."""
        visible = self.ids(candidate_id)
        chosen = visible if used is None else tuple(used)
        if not set(chosen) <= set(visible):
            raise SnapshotError("a decision cannot have used what its reader could not read")
        return observation_set_hash((i, self._items[i][1]) for i in chosen)


# --- the record -------------------------------------------------------------------------------------------------------------------------
def to_record(snapshot: Snapshot, source_urn: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger; it cannot be made before the snapshot was frozen."""
    if isinstance(created_at_ms, bool) or not isinstance(created_at_ms, int) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise SnapshotError("the creation time is a whole number of milliseconds up to the end of the year 9999")
    if created_at_ms < snapshot.snapshot_at_ms:
        raise SnapshotError("a snapshot is recorded at or after the time it was frozen")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": natural_id("sn", {"cycle": snapshot.cycle_id}),
        "source": source_urn,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": {**snapshot.body(), "sha256": snapshot.sha256},
        "refs": [],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise SnapshotError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def matches(record: dict[str, canonical.JsonValue], rebuilt: Snapshot) -> bool:
    """Whether a snapshot record is exactly what its stored items give: the replay test, and the tamper test."""
    want = {**rebuilt.body(), "sha256": rebuilt.sha256}
    return record.get("type") == TYPE and canonical.dumps({"b": record.get("body")}) == canonical.dumps({"b": want})


# --- rebuilding from the record -------------------------------------------------------------------------------------------------------
def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise SnapshotError(f"{what} is a list")
    return value


def _object(value: object, names: frozenset[str] | set[str], what: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != names:
        raise SnapshotError(f"{what} has the members {sorted(names)}")
    return value


def _refs(value: object, what: str) -> tuple[tuple[str, str], ...]:
    out = []
    for ref in _list(value, what):
        entry = _object(ref, {"evidence_id", "sha256"}, f"an item of {what}")
        out.append((str(entry["evidence_id"]), str(entry["sha256"])))
    return tuple(out)


def _unavailable(value: object) -> tuple[Unavailable, ...]:
    out = []
    for item in _list(value, "a candidate's unread sources"):
        entry = _object(item, {"source", "reason", "interval_s"}, "an unread source")
        window = entry["interval_s"]  # a window that is not a whole number of seconds is refused where the entries are validated
        out.append(Unavailable(str(entry["source"]), str(entry["reason"]), window))  # type: ignore[arg-type]
    return tuple(out)


def _candidate(value: object) -> dict[str, object]:
    names = _CANDIDATE | {"unavailable"} if isinstance(value, dict) and "unavailable" in value else _CANDIDATE
    return _object(value, names, "a candidate")


def _rejections(value: object, what: str) -> tuple[Rejection, ...]:
    out = []
    for item in _list(value, what):
        entry = _object(item, {"evidence_id", "reason"}, f"a rejection in {what}")
        try:
            out.append(Rejection(str(entry["evidence_id"]), Reject(str(entry["reason"]))))
        except ValueError:
            raise SnapshotError(f"a rejection in {what} has a reason from {[r.value for r in Reject]}") from None
    return tuple(out)


def rebuild(record: Mapping[str, object], evidence_records: Mapping[str, Mapping[str, object]]) -> Snapshot:
    """The snapshot a snapshot record describes, from the evidence records it names (as the ledger holds them).

    The replay of a cycle starts here: an arm can be asked again with the very evidence it read. Every item must be the record whose
    digest the snapshot names, and what is rebuilt must be exactly what was recorded, hash included; otherwise this refuses.
    """
    if record.get("type") != TYPE or record.get("type_version") != TYPE_VERSION:
        raise SnapshotError("not a snapshot record of a version this reader knows")
    body = _object(record.get("body"), _BODY, "a snapshot")
    try:
        taken = to_ms(parse_rfc3339(str(body["snapshot_at"])))
    except (TimeError, ValueError):
        raise SnapshotError("the snapshot time is an RFC 3339 instant") from None
    shared = _refs(body["shared"], "the shared items")
    slots_raw = [_candidate(c) for c in _list(body["candidates"], "the candidates")]
    candidate_refs = [_refs(c["items"], "a candidate's items") for c in slots_raw]
    named = {ident: digest for refs in (shared, *candidate_refs) for ident, digest in refs}
    held = []
    for ident in sorted(named):
        found = evidence_records.get(ident)
        if found is None:
            raise SnapshotError(f"the evidence record {ident} was not given")
        plain = {k: v for k, v in found.items() if k != "committed_at"}
        if record_digest(plain) != named[ident]:  # type: ignore[arg-type]
            raise SnapshotError(f"{ident} is not the record the snapshot names")
        try:
            held.append((evidence_from_record(plain), named[ident]))  # type: ignore[arg-type]
        except EvidenceError as exc:
            raise SnapshotError(f"{ident} cannot be read: {exc}") from exc
    freshness = body["freshness"]
    if not isinstance(freshness, dict):
        raise SnapshotError("the freshness limits are an object")
    slots = tuple(
        Slot(
            str(raw["candidate_id"]),
            str(raw["asset_id"]),
            tuple(ident for ident, _ in refs),
            tuple(str(m) for m in _list(raw["missing"], "a candidate's missing sources")),
            _rejections(raw["rejected"], "a candidate's rejections"),
            _unavailable(raw["unavailable"]) if "unavailable" in raw else (),
        )
        for raw, refs in zip(slots_raw, candidate_refs, strict=True)
    )
    unread = {slot.candidate_id: slot.unavailable for slot in slots if slot.unavailable}
    ordered = _unavailable_by_candidate(unread, {s.candidate_id: Candidate(s.candidate_id, s.asset_id) for s in slots})
    if ordered != unread:
        raise SnapshotError("the unread sources of a candidate are in the fixed order")
    rebuilt = Snapshot(
        str(body["cycle_id"]),
        str(body["manifest_hash"]),
        taken,
        tuple(sorted((str(kind), limit) for kind, limit in freshness.items())),
        tuple(str(r) for r in _list(body["required"], "the required sources")),
        tuple(ident for ident, _ in shared),
        _rejections(body["shared_rejected"], "the shared rejections"),
        slots,
        tuple(held),
    )
    if not matches(dict(record), rebuilt):  # type: ignore[arg-type]
        raise SnapshotError("the snapshot rebuilt from these items is not the recorded one")
    return rebuilt
