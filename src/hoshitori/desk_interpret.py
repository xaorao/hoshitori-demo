"""How the Desk reads what a check brought, as the status of the part of the claim the check was run for.

The rules are fixed and written down before use, never tuned on a case: a stated move over the hour is supported when the data shows at
least a quarter of a percent in that direction, contradicted when it shows as much the other way, and otherwise not found in the source
checked. The Desk reads one hour of one-minute bars, so a claim about a longer window is not checkable here. A source that failed is
unavailable; a source that cannot serve the asset cannot check it. Several sources for one part combine so that a disagreement shows as
one (``combine``) and is never averaged away.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from hoshitori.claim_graph import UNCHECKED
from hoshitori.desk_acquire import Acquired
from hoshitori.desk_menu import MenuCheck
from hoshitori.lineage import EvidenceStore

MIN_MOVE: Final = Decimal("0.0025")  # a fraction: a quarter of a percent
MIN_BARS: Final = 55  # of the sixty one-minute bars of the hour
WINDOW_HOURS: Final = 1
REASONS: Final = (
    "MOVE_SEEN",
    "MOVE_AGAINST",
    "MOVE_BELOW_THRESHOLD",
    "TOO_FEW_BARS",
    "FIELD_UNKNOWN",
    "WINDOW_NOT_READ",
    "FUNDING_DOES_NOT_SHOW_A_MOVE",
    "NO_INTERPRETATION",
    "SOURCE_FAILED",
    "SOURCE_CANNOT_SERVE",
)
_UNAVAILABLE: Final = frozenset({"CALL_FAILED", "TIMED_OUT", "UNREADABLE", "NOT_LIVE", "BINDING_MISMATCH"})
RULES_VERSION: Final = "desk-interpret-2"  # 2: a failed source is never outweighed by one that found nothing (1 preferred the latter)
_PREFERENCE: Final = ("SOURCE_UNAVAILABLE", "NOT_FOUND_IN_SOURCES_CHECKED", "NOT_CHECKABLE")
_RYO_HOUR: Final = "performance.change_1h_pct"
INTERPRETED: Final = ("INDEPENDENT_PRICE_CHECK", "RYO_TOKEN_READ")  # the checks whose answer says whether a stated move happened


@dataclass(frozen=True)
class NodeUpdate:
    node_id: str
    status: str
    reason: str
    evidence_ids: tuple[str, ...]
    layer: str  # NONE when no data was read, DETERMINISTIC when a fixed rule read it


def _judge(move: Decimal, direction: str) -> tuple[str, str]:
    signed = move if direction == "UP" else -move
    if signed >= MIN_MOVE:
        return "SUPPORTED", "MOVE_SEEN"
    if signed <= -MIN_MOVE:
        return "CONTRADICTED", "MOVE_AGAINST"
    return "NOT_FOUND_IN_SOURCES_CHECKED", "MOVE_BELOW_THRESHOLD"


def _ryo_move(acquired: Acquired, store: EvidenceStore) -> Decimal | None:
    for ident in acquired.evidence_ids[:1]:  # the observation item comes first
        for path, value in store.get(ident).fields:
            if path == _RYO_HOUR and isinstance(value, str):
                try:
                    return Decimal(value) / 100
                except InvalidOperation:
                    return None
    return None


def interpret(check: MenuCheck, node_id: str, direction: str, hours: int, acquired: Acquired, store: EvidenceStore) -> NodeUpdate:
    """The status a check gives the part ``node_id`` of a claim that the price moved ``direction`` over ``hours``."""
    if acquired.outcome != "OK":
        error = acquired.error_class
        if error in _UNAVAILABLE:
            return NodeUpdate(node_id, "SOURCE_UNAVAILABLE", "SOURCE_FAILED", (), "NONE")
        return NodeUpdate(node_id, "NOT_CHECKABLE", "SOURCE_CANNOT_SERVE", (), "NONE")
    ids = acquired.evidence_ids

    def update(status: str, reason: str) -> NodeUpdate:
        return NodeUpdate(node_id, status, reason, ids, "DETERMINISTIC")

    if check.check_type == "DERIVATIVES_CONTEXT":
        return update("NOT_CHECKABLE", "FUNDING_DOES_NOT_SHOW_A_MOVE")
    if check.check_type not in INTERPRETED:
        return update("NOT_CHECKABLE", "NO_INTERPRETATION")
    if hours != WINDOW_HOURS:
        return update("NOT_CHECKABLE", "WINDOW_NOT_READ")
    if check.check_type == "RYO_TOKEN_READ":
        told = _ryo_move(acquired, store)
        if told is None:
            return update("NOT_FOUND_IN_SOURCES_CHECKED", "FIELD_UNKNOWN")
        return update(*_judge(told, direction))
    series = acquired.series
    if series is None or len(series.candles) < MIN_BARS:
        return update("NOT_FOUND_IN_SOURCES_CHECKED", "TOO_FEW_BARS")
    opened, closed = Decimal(series.candles[0].open), Decimal(series.candles[-1].close)
    return update(*_judge(closed / opened - 1, direction))


def combine(statuses: Sequence[str]) -> str:
    """One status for a part from the statuses its sources gave: a disagreement is MIXED, otherwise what holds. When nothing holds, a source
    that failed comes first: the part is then incomplete and the run ends in ``FAILED_EVIDENCE``, not in an abstention (rules version 2)."""
    held, against = "SUPPORTED" in statuses, "CONTRADICTED" in statuses
    if held and against:
        return "MIXED"
    if against:
        return "CONTRADICTED"
    if held:
        return "SUPPORTED"
    for status in _PREFERENCE:
        if status in statuses:
            return status
    return UNCHECKED
