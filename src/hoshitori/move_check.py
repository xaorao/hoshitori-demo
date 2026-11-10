"""Checking a claim about a price move against the evidence the league already holds (contract 18, 34.3 and 47.3, layer 1).

The league's committed snapshots hold, for each asset it looked at, the one-, four- and 24-hour returns computed from hashed exchange
candles. A claim like "BTC fell 4% in the last 24 hours" is compared with the latest of them. The comparison is about the window that ends
when that snapshot was taken, which is stated beside every result with its age; it is not a statement about any other moment, and it is
not a check that the claim's reason is right. Direction must match, and a stated size must be within the tolerance below. The tolerance is a
prior fixed before the example claims were read and is not tuned on them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from hoshitori import snapshot
from hoshitori.claim_move import MoveAssertion
from hoshitori.evidence import EvidenceItem
from hoshitori.features import SOURCE

WINDOWS: Final = (1, 4, 24)
TOLERANCE_POINTS: Final = Decimal("0.5")  # a stated size is within this many percentage points of the move, or
TOLERANCE_SHARE: Final = Decimal("0.25")  # within this share of the stated size, whichever is larger


class MoveCheckError(ValueError):
    """Records the check cannot be made from."""


@dataclass(frozen=True)
class Held:
    """The latest committed snapshot's features item for one asset."""

    symbol: str
    item: EvidenceItem
    evidence_id: str
    cycle_id: str
    snapshot_at_ms: int


@dataclass(frozen=True)
class MoveResult:
    status: (
        str  # an observation status: SUPPORTED, CONTRADICTED, NOT_CHECKABLE, NOT_FOUND_IN_SOURCES_CHECKED, SOURCE_UNAVAILABLE, UNRESOLVED
    )
    reason: str  # in words, with the numbers
    evidence_id: str | None
    cycle_id: str | None
    snapshot_at_ms: int | None
    moved_percent: Decimal | None  # what the evidence says, signed


def _percent(value: Decimal) -> str:
    return f"{value:+.2f}%"


def check_move(assertion: MoveAssertion, held: Mapping[str, Held]) -> MoveResult:
    """The assertion judged against the held evidence."""
    window = assertion.window_hours
    asset = assertion.asset
    if asset is None or window is None or assertion.direction is None:
        return MoveResult("UNRESOLVED", f"the words leave open: {', '.join(assertion.open)}", None, None, None, None)
    if window not in WINDOWS:
        return MoveResult(
            "NOT_CHECKABLE", f"the league holds moves over {', '.join(str(w) for w in WINDOWS)} hours, not {window}", None, None, None, None
        )
    if asset not in held:
        return MoveResult("NOT_FOUND_IN_SOURCES_CHECKED", f"no committed snapshot holds {asset}", None, None, None, None)
    h = held[asset]
    raw = h.item.field(f"ret_{window}h")
    if not isinstance(raw, str):
        return MoveResult(
            "SOURCE_UNAVAILABLE",
            f"the {window}-hour move of {asset} is unknown in the latest snapshot",
            h.evidence_id,
            h.cycle_id,
            h.snapshot_at_ms,
            None,
        )
    moved = Decimal(raw) * 100
    here = (h.evidence_id, h.cycle_id, h.snapshot_at_ms, moved)
    sign = (moved > 0) - (moved < 0)  # 1 for a rise, -1 for a fall, 0 for no move at all
    if sign != (1 if assertion.direction == "UP" else -1):
        return MoveResult("CONTRADICTED", f"{asset} moved {_percent(moved)} over the {window} hours, the other way", *here)
    size = assertion.size_percent
    if size is None:
        return MoveResult("SUPPORTED", f"{asset} moved {_percent(moved)} over the {window} hours, in that direction", *here)
    tolerance = max(TOLERANCE_POINTS, size * TOLERANCE_SHARE)
    if abs(abs(moved) - size) <= tolerance:
        return MoveResult("SUPPORTED", f"{asset} moved {_percent(moved)} over the {window} hours; the claim says {size}%", *here)
    return MoveResult("CONTRADICTED", f"{asset} moved {_percent(moved)} over the {window} hours; the claim says {size}%", *here)


def held_from(records: Sequence[Mapping[str, Any]]) -> dict[str, Held]:
    """The latest committed snapshot's features item of each asset, from the ledger's records."""
    evidence = {r["id"]: r for r in records if r.get("type") == "evidence"}
    manifests = {r["body"]["cycle_id"]: r for r in records if r.get("type") == "manifest"}
    seen: dict[str, list[Held]] = {}
    for record in records:
        if record.get("type") != snapshot.TYPE:
            continue
        cycle_id = record["body"]["cycle_id"]
        manifest = manifests.get(cycle_id)
        if manifest is None:
            raise MoveCheckError("the ledger holds a snapshot without its manifest")
        try:
            frozen = snapshot.rebuild(record, evidence)
        except snapshot.SnapshotError as exc:
            raise MoveCheckError(f"the snapshot of {cycle_id} cannot be rebuilt: {exc}") from exc
        symbols = {e["candidate_id"]: e["symbol"] for e in manifest["body"]["entries"] if e["inclusion"] == "INCLUDED"}
        by_id = {item.evidence_id: item for item, _ in frozen.items}
        for slot in frozen.slots:
            for ident in slot.item_ids:
                item = by_id[ident]
                if item.source == SOURCE:  # only the features item has this source
                    symbol = symbols[slot.candidate_id]
                    seen.setdefault(symbol, []).append(Held(symbol, item, ident, cycle_id, frozen.snapshot_at_ms))
    return {symbol: max(found, key=lambda h: h.snapshot_at_ms) for symbol, found in seen.items()}
