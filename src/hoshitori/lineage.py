"""The evidence store: items by id and digest, their lineage, reads as of a time, and the count of independent origins.

``DATA_PROVENANCE`` sections 3 to 5 and contract sections 29 and 40 are the rules. The store holds nothing the ledger does
not: an item is added with the digest of its record, a derived item can only be added after its parents and with their
digests, and nothing is ever replaced.

* **Lineage is declared and checked.** A parent must be in the store, with the digest the child names, and must have been
  known no later than the child. Because a parent has to exist first, a cycle cannot be written; a child that names a parent
  which is not there, or names it with another digest, is refused (a swapped parent would otherwise go unseen).
* **Descendants** are what an intervention must remove with a source. After one, ``leaks`` lists what is left in a context
  that still stands on a removed item, so a derived value cannot carry a withheld source back into a decision.
* **As of a time** a key's reading is the item with the greatest ``known_at`` that is not after that time; a revision is a
  later item and never changes what an earlier time saw. A decision keeps the hash of the exact set it used.
* **Independence** counts distinct origin families, not calls: a derived item counts as the families of its roots, an item
  that repeats an original counts as the original's family, families known to share an upstream count once, and a pair that
  nobody has shown to be independent is reported as not established.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from hoshitori import canonical
from hoshitori.evidence import EvidenceError, EvidenceItem, Family, Kind, from_record, record_digest

_SHA256 = re.compile(r"[0-9a-f]{64}")


class LineageError(ValueError):
    """A write the store refuses, or a question about an item it does not hold."""


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise LineageError("a digest is 64 lower-case hexadecimal characters")
    return value


@dataclass(frozen=True)
class Independence:
    """How many different origins stand behind a set of items, and how sure we are that they really are different."""

    origins: tuple[Family, ...]
    calls: int
    established: bool

    @property
    def count(self) -> int:
        return len(self.origins)

    def display(self) -> str:
        """ "N independent origins checked (M calls)", with RYO as one origin; never "3 sources agree" for three calls to one."""
        origins = f"{self.count} independent {'origin' if self.count == 1 else 'origins'}"
        text = f"{origins} checked ({self.calls} {'call' if self.calls == 1 else 'calls'})"
        return text if self.established else f"{text}; independence not established"


def observation_set_hash(entries: Iterable[tuple[str, str]]) -> str:
    """The hash a decision keeps of the exact items it saw: ids with the digests of their records, in any order, once each."""
    unique: dict[str, str] = {}
    for ident, digest in entries:
        if not isinstance(ident, str) or not ident:
            raise LineageError("an id is a non-empty string")
        if unique.setdefault(ident, _digest(digest)) != digest:
            raise LineageError(f"{ident} is given two digests")
    body: dict[str, canonical.JsonValue] = {"items": [{"evidence_id": i, "sha256": unique[i]} for i in sorted(unique)]}
    return hashlib.sha256(canonical.dumps(body)).hexdigest()


class EvidenceStore:
    def __init__(self) -> None:
        self._digests: dict[str, str] = {}
        self._items: dict[str, EvidenceItem] = {}
        self._children: dict[str, list[str]] = {}
        self._replaced_by: dict[str, str] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, ident: object) -> bool:
        return ident in self._items

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def get(self, ident: str) -> EvidenceItem:
        try:
            return self._items[ident]
        except KeyError:
            raise LineageError(f"there is no item {ident!r}") from None

    def digest(self, ident: str) -> str:
        self.get(ident)
        return self._digests[ident]

    # --- writing ---------------------------------------------------------------------------------------------------------------------
    def add(self, item: EvidenceItem, digest: str) -> bool:
        """Add an item with the digest of its record. ``True`` if it is new, ``False`` if the same item was already there."""
        _digest(digest)
        ident = item.evidence_id
        if ident in self._items:
            if self._digests[ident] != digest:
                raise LineageError(f"{ident} is already held with another digest")
            return False
        for parent_id, parent_digest in item.derived_from:
            parent = self._items.get(parent_id)
            if parent is None:
                raise LineageError(f"the parent {parent_id} is not in the store")
            if self._digests[parent_id] != parent_digest:
                raise LineageError(f"the parent {parent_id} is named with a digest other than the one held")
            if parent.known_at_ms > item.known_at_ms:
                raise LineageError(f"the parent {parent_id} was known after the item derived from it")
        if item.supersedes is not None:
            old = self._items.get(item.supersedes)
            if old is None:
                raise LineageError(f"the item it supersedes, {item.supersedes}, is not in the store")
            if old.key != item.key:
                raise LineageError("a revision measures the same thing as the item it replaces")
            if old.known_at_ms >= item.known_at_ms:
                raise LineageError("a revision is known later than the item it replaces")
            if item.supersedes in self._replaced_by:
                raise LineageError(f"{item.supersedes} has already been superseded")
            self._replaced_by[item.supersedes] = ident
        self._items[ident] = item
        self._digests[ident] = digest
        self._children.setdefault(ident, [])
        for parent_id, _ in item.derived_from:
            self._children[parent_id].append(ident)
        return True

    def add_record(self, record: dict[str, canonical.JsonValue]) -> bool:
        try:
            item = from_record(record)
        except EvidenceError as exc:
            raise LineageError(str(exc)) from exc
        return self.add(item, record_digest(record))

    # --- the graph -------------------------------------------------------------------------------------------------------------------
    def parents(self, ident: str) -> tuple[str, ...]:
        return tuple(parent for parent, _ in self.get(ident).derived_from)

    def children(self, ident: str) -> tuple[str, ...]:
        self.get(ident)
        return tuple(sorted(self._children[ident]))

    def ancestors(self, ident: str) -> frozenset[str]:
        found: set[str] = set()
        stack = list(self.parents(ident))
        while stack:
            current = stack.pop()
            if current not in found:
                found.add(current)
                stack.extend(self.parents(current))
        return frozenset(found)

    def descendants(self, ident: str) -> frozenset[str]:
        found: set[str] = set()
        stack = list(self.children(ident))
        while stack:
            current = stack.pop()
            if current not in found:
                found.add(current)
                stack.extend(self._children[current])
        return frozenset(found)

    def roots(self, ident: str) -> frozenset[str]:
        """The items that are not derived from anything that this one stands on: itself if it is not derived."""
        item = self.get(ident)
        if item.kind is not Kind.DERIVED:
            return frozenset({ident})
        return frozenset(root for root in self.ancestors(ident) if self.get(root).kind is not Kind.DERIVED)

    def families(self, ident: str) -> frozenset[Family]:
        return frozenset(self.get(root).origin for root in self.roots(ident))

    def withheld(self, removed: Iterable[str]) -> frozenset[str]:
        """What an intervention removes when it removes ``removed``: those items and everything derived from any of them."""
        closure: set[str] = set()
        for ident in removed:
            closure.add(ident)
            closure |= self.descendants(ident)
        return frozenset(closure)

    def leaks(self, context: Iterable[str], removed: Iterable[str]) -> tuple[str, ...]:
        """The items of a context that still stand on something removed. Empty means the intervention is clean."""
        gone = self.withheld(removed)
        held = sorted(set(context))
        for ident in held:
            self.get(ident)  # a context that names something the store does not hold is a mistake, not a clean context
        return tuple(ident for ident in held if ident in gone)

    # --- reading as of a time --------------------------------------------------------------------------------------------------------
    def as_of(self, at_ms: int, kinds: Iterable[Kind] | None = None) -> tuple[EvidenceItem, ...]:
        """For each key, the newest item known no later than ``at_ms``; nothing that was learnt afterwards, whatever time it describes."""
        allowed = None if kinds is None else frozenset(kinds)
        readings: dict[tuple[str, str | None, str, str], list[EvidenceItem]] = {}
        for item in self._items.values():
            if item.known_at_ms <= at_ms and (allowed is None or item.kind in allowed):
                readings.setdefault(item.key, []).append(item)
        newest = [max(found, key=lambda item: (item.known_at_ms, item.received_at_ms, item.evidence_id)) for found in readings.values()]
        return tuple(sorted(newest, key=lambda item: item.evidence_id))

    def latest(self, ident: str, at_ms: int) -> EvidenceItem:
        """The newest revision of an item that was known by ``at_ms``; the item itself if no revision was known yet."""
        current = self.get(ident)
        while current.evidence_id in self._replaced_by:
            following = self._items[self._replaced_by[current.evidence_id]]
            if following.known_at_ms > at_ms:
                break
            current = following
        return current

    def set_hash(self, ids: Iterable[str]) -> str:
        return observation_set_hash((ident, self.digest(ident)) for ident in ids)

    # --- independent origins ---------------------------------------------------------------------------------------------------------
    def independence(
        self,
        items: Iterable[str],
        *,
        originals: Mapping[str, Family] | None = None,
        shared: Iterable[frozenset[Family]] = (),
        independent: Iterable[frozenset[Family]] = (),
    ) -> Independence:
        """The origins behind ``items``.

        ``originals`` names, for an item that repeats another piece of information, the family of that original; ``shared`` lists groups of
        families known to draw on one upstream (counted once); ``independent`` lists pairs of families that someone has shown to have
        separate upstreams. Two origins that are in neither list are counted as two and reported as not established.
        """
        repr_of: dict[Family, Family] = {}
        for group in shared:
            leader = min(group)
            for family in group:
                repr_of[family] = leader
        ids = set(items)
        origins: set[Family] = set()
        for ident in ids:
            families = frozenset({originals[ident]}) if originals is not None and ident in originals else self.families(ident)
            origins |= {repr_of.get(family, family) for family in families}
        ordered = tuple(sorted(origins))
        known = {frozenset({repr_of.get(a, a), repr_of.get(b, b)}) for a, b in (tuple(pair) for pair in independent)}
        pairs = [frozenset({a, b}) for position, a in enumerate(ordered) for b in ordered[position + 1 :]]
        return Independence(ordered, len(ids), all(pair in known for pair in pairs))


def rebuild(records: Iterable[dict[str, canonical.JsonValue]]) -> EvidenceStore:
    """A store from records in ledger order. A record delivered twice counts once; a parent must come before its child."""
    store = EvidenceStore()
    for record in records:
        store.add_record(record)
    return store
