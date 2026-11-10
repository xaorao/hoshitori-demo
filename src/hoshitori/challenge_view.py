"""The evidence challenge of the rules arm, for every candidate of a committed cycle, as rows the site can show.

For each candidate and horizon the rules arm decided on, and for each source that stood in the cycle's frozen snapshot, the source is
taken out (with everything derived from it), the same rule is asked again and what changed is written in plain words. The rules arm is
a fixed function, so each answer is exact. It says nothing about the model arms, which are not asked here, and nothing about whether
the evidence was true or the market moved for it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, cast

from hoshitori import canonical, labels, lineage, rules_arm, rules_challenge, rules_sensitivity, snapshot
from hoshitori.intervention import source_ids
from hoshitori.protocol import Protocol
from hoshitori.view_models import percent


class ChallengeViewError(ValueError):
    """A cycle the challenge cannot be made for."""


@dataclass(frozen=True)
class Row:
    cycle_id: str
    arm: str
    symbol: str
    hours: int
    source: str
    items: int  # how many items left the snapshot with it, derived ones included
    outcome: str
    words: str
    original: str
    changed: str | None


def describe(decision: rules_arm.Decision) -> str:
    """What the rule decided, in a few words."""
    if decision.verdict is rules_arm.Verdict.CALL and decision.p is not None:
        return f"{percent(decision.p)} up"
    if decision.verdict is rules_arm.Verdict.FAILED_EVIDENCE:
        return "No call: the evidence it needs was missing"
    return "No call"


def ordered(names: Iterable[str]) -> list[str]:
    """The distinct names, alphabetically, so the rows come in the same order every time."""
    return sorted(set(names))


def cycle_of(records: Sequence[Mapping[str, Any]], cycle_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any], snapshot.Snapshot]:
    """The snapshot record, the manifest record and the rebuilt snapshot of a committed cycle, or the reason they cannot be had."""
    taken = next((r for r in records if r.get("type") == snapshot.TYPE and r["body"]["cycle_id"] == cycle_id), None)
    manifest = next((r for r in records if r.get("type") == "manifest" and r["body"]["cycle_id"] == cycle_id), None)
    if taken is None or manifest is None:
        raise ChallengeViewError("the ledger holds no snapshot and manifest of this cycle")
    evidence = {r["id"]: r for r in records if r.get("type") == "evidence"}
    try:
        return taken, manifest, snapshot.rebuild(taken, evidence)
    except snapshot.SnapshotError as exc:
        raise ChallengeViewError(f"the snapshot of the cycle cannot be rebuilt: {exc}") from exc


def store_of(records: Sequence[Mapping[str, Any]]) -> lineage.EvidenceStore:
    plain = [
        {k: v for k, v in r.items() if k != "committed_at"} for r in records if r.get("type") == "evidence"
    ]  # as the digests name them
    return lineage.rebuild(cast(list[dict[str, canonical.JsonValue]], plain))


def rows(
    records: Sequence[Mapping[str, Any]], cycle_id: str, protocol: Protocol, mappings: Mapping[str, rules_arm.BaseRates]
) -> tuple[Row, ...]:
    """Every challenge of the rules arms for one committed cycle, in the order candidate, arm, horizon, source."""
    _, manifest, frozen = cycle_of(records, cycle_id)
    store = store_of(records)
    symbols = {e["candidate_id"]: e["symbol"] for e in manifest["body"]["entries"] if e["inclusion"] == "INCLUDED"}
    sources = ordered(item.source for item, _ in frozen.items)
    out: list[Row] = []
    for slot in frozen.slots:
        for arm, _ in protocol.arms:
            for hours in protocol.horizons_hours:
                for source in sources:
                    result = rules_challenge.challenge(frozen, store, source_ids(frozen, source), mappings[arm], slot.candidate_id, hours)
                    out.append(
                        Row(
                            cycle_id,
                            arm,
                            symbols[slot.candidate_id],
                            hours,
                            source,
                            len(result.intervention.withheld),
                            result.outcome,
                            result.words(),
                            describe(result.original),
                            None if result.changed is None else describe(result.changed),
                        )
                    )
    return tuple(out)


@dataclass(frozen=True)
class Move:
    direction: str  # UP or DOWN
    state: str  # in words
    at: str  # the return at which the rule would be in that state, as a signed percentage
    distance: str  # how far the return would have had to move, in percentage points
    says: str  # what the rule would have said there


@dataclass(frozen=True)
class Boundary:
    cycle_id: str
    arm: str
    symbol: str
    hours: int
    ret: str  # the return the rule saw, as a signed percentage
    state: str  # in words
    says: str
    moves: tuple[Move, ...]  # only the moves that would have changed the call; none when every neighbouring state says the same


def _points(value: Decimal) -> str:
    """A return as a signed percentage to two places, half to even; a value that rounds to nothing is written without a sign."""
    shown = (value * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return f"{abs(shown):.2f}" if shown == 0 else f"{shown:+.2f}"


def _gap(value: Decimal) -> str:
    """How far a return would have had to move: as ``_points``, but a move too small to show says so instead of reading as no move."""
    shown = _points(value)
    if value != 0 and shown == "0.00":
        return ("-" if value.is_signed() else "+") + "<0.01"
    return shown


def _says(p: Decimal | None, action: str | None) -> str:
    if p is None or action is None:
        return "no call: too few past windows in that state"
    return f"{percent(p)} up, {'long' if action == rules_arm.Action.LONG.value else 'flat'}"


def boundaries(
    records: Sequence[Mapping[str, Any]], cycle_id: str, protocol: Protocol, mappings: Mapping[str, rules_arm.BaseRates]
) -> tuple[Boundary, ...]:
    """For every call of the rules arm in a committed cycle: how far the move was from changing the call, and what the rule would say.

    A move is the nearest return, each way, at which the call would have been different: states in between that say the same (the same
    chance and action, or no call in both) are passed over, so the move may be more than one state away, and a call that no state in a
    direction would change has no move that way."""
    _, manifest, frozen = cycle_of(records, cycle_id)
    reader = snapshot.Reader(frozen, snapshot.Access.INDEPENDENT)
    symbols = {e["candidate_id"]: e["symbol"] for e in manifest["body"]["entries"] if e["inclusion"] == "INCLUDED"}
    out: list[Boundary] = []
    for slot in frozen.slots:
        for arm, _ in protocol.arms:
            for hours in protocol.horizons_hours:
                s = rules_sensitivity.sensitivity(reader, slot.candidate_id, mappings[arm], hours)
                if s is None:
                    continue
                said = (s.p, rules_sensitivity.action_of(s.p))
                moves = tuple(
                    Move(n.direction, labels.RULE_STATE_WORDS[n.state], _points(n.ret), _gap(n.distance), _says(n.p, n.action))
                    for n in s.changes
                )
                out.append(
                    Boundary(
                        cycle_id,
                        arm,
                        symbols[slot.candidate_id],
                        hours,
                        _points(s.ret),
                        labels.RULE_STATE_WORDS[s.state],
                        _says(*said),
                        moves,
                    )
                )
    return tuple(out)
