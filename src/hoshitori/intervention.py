"""Taking evidence out of a frozen snapshot to ask what would have happened without it (contract 13 and 29, ``EVIDENCE_CHALLENGE_SPEC``).

Removing a source is not removing its raw items only: everything derived from them stands on them too, and a decision that read a
derived value has read the source. So an intervention removes the items named and every descendant, then checks, by walking the
lineage the other way (from each item left to its ancestors), that nothing left stands on something removed. A graph that fails that
check is NOT_EVALUATED and no snapshot is returned: a result built on it would not be a result.

What comes back is a what-if snapshot. It is never stored, never committed and never read by any arm of the league; it has another
hash than the one a decision was made on, and the original is left exactly as it was. A required source taken out is missing in it, as
it would be in a snapshot frozen without it, so an arm ends that candidate as it would end any candidate without a required source.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final

from hoshitori.evidence import MAX_VALUE, EvidenceItem, Family, FamilyKind, Kind, record_digest
from hoshitori.lineage import EvidenceStore
from hoshitori.snapshot import Snapshot, missing_sources

READY: Final = "READY"
NOT_EVALUATED: Final = "NOT_EVALUATED"
PLACEBO_SOURCE: Final = "hoshitori.placebo"
PLACEBO_SENTENCE: Final = "This reading carries no information about the token. "
PLACEBO_URN: Final = "urn:hoshitori:placebo"


class InterventionError(ValueError):
    """An intervention that asks for something the snapshot does not hold, or asks for nothing."""


@dataclass(frozen=True)
class Withheld:
    status: str  # READY, or NOT_EVALUATED when something left in the snapshot still stands on something removed
    snapshot: Snapshot | None  # the what-if snapshot; none when not evaluated
    removed: tuple[str, ...]  # what was asked for
    withheld: tuple[str, ...]  # what left the snapshot: those and everything derived from them
    leaks: tuple[str, ...]  # what is left and still stands on something removed (empty when READY)


def source_ids(snapshot: Snapshot, source: str) -> tuple[str, ...]:
    """The items of one source in the snapshot, by id; a source that is not there is an error, not an empty intervention."""
    found = tuple(sorted(item.evidence_id for item, _ in snapshot.items if item.source == source))
    if not found:
        raise InterventionError(f"the snapshot holds nothing from {source!r}")
    return found


def withhold(snapshot: Snapshot, store: EvidenceStore, removed: Iterable[str]) -> Withheld:
    """The snapshot without these items and everything derived from them."""
    asked = tuple(sorted(set(removed)))
    if not asked:
        raise InterventionError("an intervention removes at least one item")
    held = {item.evidence_id for item, _ in snapshot.items}
    missing = [i for i in asked if i not in held]
    if missing:
        raise InterventionError(f"{missing[0]} is not in the snapshot")
    gone = store.withheld(asked)
    left = tuple((item, digest) for item, digest in snapshot.items if item.evidence_id not in gone)
    standing = tuple(sorted(item.evidence_id for item, _ in left if store.ancestors(item.evidence_id) & set(asked)))
    out = tuple(sorted(held & gone))
    if standing:
        return Withheld(NOT_EVALUATED, None, asked, out, standing)
    kept = {item.evidence_id: item for item, _ in left}
    slots = tuple(
        replace(s, item_ids=ids, missing=missing_sources(snapshot.required, (kept[i] for i in ids)))
        for s, ids in ((s, tuple(i for i in s.item_ids if i not in gone)) for s in snapshot.slots)
    )
    what_if = replace(snapshot, slots=slots, shared=tuple(i for i in snapshot.shared if i not in gone), items=left)
    return Withheld(READY, what_if, asked, out, ())


@dataclass(frozen=True)
class Placed:
    snapshot: Snapshot  # the what-if snapshot with the placebo in it; never stored, committed or read by an arm of the league
    item_id: str
    matched: tuple[str, ...]  # the items whose text the placebo is as long as
    text_chars: int


def add_placebo(snapshot: Snapshot, candidate_id: str, like: Iterable[str]) -> Placed:
    """The snapshot with one neutral item added to a candidate, as long as the text of the items ``like``.

    It is the control of a challenge that takes evidence out: a decision that moves when only text of this size is added, with nothing in
    it, moves for a reason that has nothing to do with what the evidence said. The item says nothing about any token, belongs to no
    asset and stands on no other item; it is a reading of its own source, known when the snapshot was fixed, and the what-if snapshot is
    not the one a decision was made on. A snapshot holds at most one."""
    asked = tuple(sorted(set(like)))
    if not asked:
        raise InterventionError("a placebo is sized from at least one item")
    held = {item.evidence_id: item for item, _ in snapshot.items}
    missing = [i for i in asked if i not in held]
    if missing:
        raise InterventionError(f"{missing[0]} is not in the snapshot")
    if any(item.source == PLACEBO_SOURCE for item in held.values()):
        raise InterventionError("the snapshot already holds a placebo")
    slot = next((s for s in snapshot.slots if s.candidate_id == candidate_id), None)
    if slot is None:
        raise InterventionError(f"{candidate_id} is not a candidate of this snapshot")
    size = min(MAX_VALUE, max(1, sum(len(v) for i in asked for _, v in held[i].fields if isinstance(v, str))))
    note = (PLACEBO_SENTENCE * (size // len(PLACEBO_SENTENCE) + 1))[:size]
    item = EvidenceItem(
        adapter_version="placebo-1",
        path="placebo/neutral",
        kind=Kind.OBSERVATION,
        source=PLACEBO_SOURCE,
        origin=Family(FamilyKind.PRIMARY_DOC, "hoshitori-placebo"),
        received_at_ms=snapshot.snapshot_at_ms,
        known_at_ms=snapshot.snapshot_at_ms,
        fields=(("note", note),),
        capture_id="placebo",
        raw_sha256=hashlib.sha256(note.encode()).hexdigest(),
    )
    digest = record_digest(item.to_record(PLACEBO_URN, snapshot.snapshot_at_ms))
    slots = tuple(replace(s, item_ids=(*s.item_ids, item.evidence_id)) if s.candidate_id == candidate_id else s for s in snapshot.slots)
    what_if = replace(snapshot, slots=slots, items=(*snapshot.items, (item, digest)))
    return Placed(what_if, item.evidence_id, asked, size)
