"""The Today impact ranking, version one: deterministic, explainable, provisional (contract sections 22 and 38; plan, part B).

The objective is the research decision (investigate, watch or dismiss), not a price move. An item's score is a sum of components that
come from records the product holds. A component the product cannot compute is listed as unknown and contributes nothing: it is never
turned into zero by pretence. The weights are priors written before any validation. **The ranking is provisional**: no expert agreement
has been measured, none is shown, and the page says so until an independent validation exists (``LABEL``).

The order of the list is a decision about a set, not a sort of independent scores:

* the first investigated thesis (or updated call) with the highest score is featured;
* the highest-scoring development not yet checked is the reserved row beneath it (equal scores keep the cycle's order);
* the rest follow by score, then in the order they came, with at most two items about one token among the first five unless an
  item's score is at or above ``HIGH`` (a separate development of high consequence is never hidden by a repetition limit).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from hoshitori.today_items import (
    BREAKING_UNVERIFIED,
    INVESTIGATED_THESIS,
    MATERIAL_UPDATE,
    TodayItem,
)

FORMULA_VERSION: Final = "impact/1"
LABEL: Final = "Provisional research-impact ranking; independent validation pending."
PREMISE_CHANGE: Final = 10
CONTRADICTION: Final = 6
NEW_ORIGIN: Final = 2
CONFLICT: Final = 4
UNCHECKED: Final = 3
CAPS: Final = {"CONTRADICTION": 3, "NEW_ORIGIN": 4}
HIGH: Final = 14
OVERLAP_LIMIT: Final = 2
TOP: Final = 5
COMPONENTS: Final = ("PREMISE_CHANGE", "CONTRADICTION", "NEW_ORIGIN", "CONFLICT", "UNCHECKED")
UNKNOWN_COMPONENTS: Final = (
    "INVALIDATION_PROXIMITY",
    "COVERAGE_LOSS",
    "CORRECTION_OR_RETRACTION",
    "TRIAGE_MARGIN",
    "PRACTICE_POSITION_EFFECT",
)
CONFLICT_REASON: Final = "EVIDENCE_CONFLICT_UNRESOLVED"
SENTENCES: Final = {
    "PREMISE_CHANGE": "A premise of a recorded call changed.",
    "CONTRADICTION": "Independent origins that contradict the move: {n}.",
    "NEW_ORIGIN": "Independent origins read: {n}.",
    "CONFLICT": "The checks disagreed and the disagreement stayed unresolved.",
    "UNCHECKED": "Nothing about it has been checked yet.",
}


def contributions(item: TodayItem) -> tuple[tuple[str, int, int], ...]:
    """``(component, count, points)`` for every component that contributes, in the fixed order of ``COMPONENTS``."""
    found: list[tuple[str, int, int]] = []
    if item.state == MATERIAL_UPDATE:
        found.append(("PREMISE_CHANGE", 1, PREMISE_CHANGE))
    contradicting = min(item.contradicting, CAPS["CONTRADICTION"])
    if contradicting > 0:
        found.append(("CONTRADICTION", contradicting, contradicting * CONTRADICTION))
    read = min(item.origins_read, CAPS["NEW_ORIGIN"])
    if read > 0:
        found.append(("NEW_ORIGIN", read, read * NEW_ORIGIN))
    if item.reason == CONFLICT_REASON:
        found.append(("CONFLICT", 1, CONFLICT))
    if item.state == BREAKING_UNVERIFIED:
        found.append(("UNCHECKED", 1, UNCHECKED))
    return tuple(found)


def score(item: TodayItem) -> int:
    return sum(points for _, _, points in contributions(item))


def reasons(item: TodayItem) -> tuple[str, ...]:
    """The sentences that say why the item scores as it does, the largest contribution first (ties by the fixed component order)."""
    order = {name: n for n, name in enumerate(COMPONENTS)}
    ranked = sorted(contributions(item), key=lambda c: (-c[2], order[c[0]]))
    return tuple(SENTENCES[name].format(n=count) for name, count, _ in ranked)


def scored(item: TodayItem) -> TodayItem:
    return replace(item, score=score(item), reasons=reasons(item))


def slate(items: tuple[TodayItem, ...]) -> tuple[TodayItem, ...]:
    """The list in its printed order. Ties keep the order the items came in."""
    pool = [scored(i) for i in items]
    featured = [i for i in pool if i.state in (INVESTIGATED_THESIS, MATERIAL_UPDATE)]
    top = max(featured, key=lambda i: i.score, default=None)  # the first of equal scores: max keeps the earliest
    reserved_pool = [i for i in pool if i.state == BREAKING_UNVERIFIED]
    reserved = max(reserved_pool, key=lambda i: i.score, default=None)
    chosen: list[TodayItem] = []
    for item in (top, reserved):
        if item is not None:
            chosen.append(item)
            pool.remove(item)
    rest = sorted(pool, key=lambda i: -i.score)  # a stable sort: equal scores keep their order
    seen: dict[str, int] = {}
    for item in chosen:
        seen[item.symbol] = seen.get(item.symbol, 0) + 1
    ordered: list[TodayItem] = list(chosen)
    deferred: list[TodayItem] = []
    for item in rest:
        crowded = len(ordered) < TOP and seen.get(item.symbol, 0) >= OVERLAP_LIMIT and item.score < HIGH
        if crowded:
            deferred.append(item)
            continue
        ordered.append(item)
        seen[item.symbol] = seen.get(item.symbol, 0) + 1
    return (*ordered, *deferred)
