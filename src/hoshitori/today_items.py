"""The developments list of Today: what happened, whether anyone has checked it, and where the check stands (contract sections 33 and 38).

The five item states of section 33 are derived from records the product really holds.

* ``BREAKING_UNVERIFIED``: an ``EVENT`` entry of a sealed manifest (a registry trigger) that no Desk run answers. It carries no stance, no
  probability and no verdict: only what happened, from which rule, and that nothing has been checked.
* ``BEING_CHECKED``: a Desk run with no terminal state yet.
* ``NO_RELIABLE_CONCLUSION``: a Desk run that ended abstained or failed; its reason is kept.
* ``INVESTIGATED_THESIS``: a Desk run that ended committed.
* ``MATERIAL_UPDATE``: a thesis revision (``post_snapshot``) that says a premise of a recorded call changed after the call was made;
  the call itself is unchanged and stays in its place.

The first investigated thesis is the featured item; the newest unchecked one is the reserved row directly beneath it (contract section 38),
so an unchecked development is never last behind every checked one.

A Desk run answers an event only when its trigger id is the event's trigger id: two events of one token are two items, and a run that
started from no trigger answers none. Every sentence is a closed template: no text from a source reaches an item. The order is the
provisional research-impact ranking of ``impact`` (a printed sum of parts from real records; a part the product cannot compute is left
out); items of equal score keep the order they are collected in, and the page says the ranking is provisional.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from hoshitori import desk_view

BREAKING_UNVERIFIED: Final = "BREAKING_UNVERIFIED"
BEING_CHECKED: Final = "BEING_CHECKED"
NO_RELIABLE_CONCLUSION: Final = "NO_RELIABLE_CONCLUSION"
INVESTIGATED_THESIS: Final = "INVESTIGATED_THESIS"
MATERIAL_UPDATE: Final = "MATERIAL_UPDATE"
STATES: Final = (
    INVESTIGATED_THESIS,
    BEING_CHECKED,
    NO_RELIABLE_CONCLUSION,
    BREAKING_UNVERIFIED,
)  # the states a Desk run or an event can have
ALL_STATES: Final = (*STATES, MATERIAL_UPDATE)

UNRANKED: Final = 10**9  # an event route with no rank sorts after every ranked one
RULE: Final = (
    "Ordered by a provisional research-impact score (impact/1), not by time: the highest-scoring investigated or updated call first, "
    "the highest-scoring development not yet checked directly beneath it, then the rest by score, with at most two of the top five about "
    "one token unless an item scores high. Equal scores keep the order they arrived in. The score is a sum of printed parts; a part the "
    "product cannot compute is left out, never counted as zero. Provisional research-impact ranking; independent validation pending."
)
UNKNOWN: Final = {
    BREAKING_UNVERIFIED: "Nothing about this has been checked.",
    BEING_CHECKED: "The checks are still running.",
    NO_RELIABLE_CONCLUSION: "The checks did not reach a reliable conclusion.",
    INVESTIGATED_THESIS: "The thesis is a practice call, not a statement that it is true.",
    MATERIAL_UPDATE: "The recorded call is unchanged; the part of it that settles it may no longer be available.",
}
HEADLINE: Final = {
    BREAKING_UNVERIFIED: "A listing changed on a venue.",
    BEING_CHECKED: "The Desk is checking a move.",
    NO_RELIABLE_CONCLUSION: "The Desk checked a move and reached no reliable conclusion.",
    INVESTIGATED_THESIS: "The Desk checked a move and recorded a practice call.",
    MATERIAL_UPDATE: "A premise of a recorded call changed after it was made.",
}


class ItemError(ValueError):
    """A Desk run or a manifest that does not map to an item."""


@dataclass(frozen=True)
class TodayItem:
    item_id: str
    state: str
    symbol: str
    route: str
    headline: str
    first_seen: str | None  # when the registry first saw an unchecked trigger; none for a Desk run
    reason: str | None  # the closed reason a run ended with, when it did not commit
    link: str | None  # the Desk run it opens
    unknown: str
    label: str | None = None  # how the run was made (live, recorded or synthetic); none for an unchecked event
    contradicting: int = 0  # independent source families whose answer contradicted the move
    origins_read: int = 0  # independent source families that answered about the move
    score: int = 0  # the impact ranking's score, set when the list is ordered
    reasons: tuple[str, ...] = ()  # why it scores so, the largest contribution first


def _run_item(run: desk_view.DeskRun) -> TodayItem:
    if run.open:
        state = BEING_CHECKED
    elif run.state == "COMMITTED":
        state = INVESTIGATED_THESIS
    elif run.state in ("ABSTAINED", "FAILED", "FAILED_FINAL"):
        state = NO_RELIABLE_CONCLUSION
    else:
        raise ItemError(f"a Desk run ended in an unknown state: {run.state!r}")
    return TodayItem(
        f"run-{run.run_id}",
        state,
        run.symbol,
        run.route,
        HEADLINE[state],
        None,
        run.reason,
        run.run_id,
        UNKNOWN[state],
        run.label,
        len(run.origins.contradicting),
        len(run.origins.read),
    )


def unchecked(manifests: Sequence[Mapping[str, Any]], runs: Sequence[desk_view.DeskRun]) -> list[TodayItem]:
    """One item per included event trigger of the sealed manifests that no Desk run answers, in the manifest's order (smaller rank first)"""
    answered = {run.trigger_id for run in runs if run.trigger_id is not None}
    found: dict[str, tuple[tuple[int, str, str], TodayItem]] = {}
    for record in manifests:
        if record.get("type") != "manifest":
            continue
        for entry in record["body"]["entries"]:
            for route in entry["routes"]:
                trigger = route["trigger_id"]
                if route["route"] != "EVENT" or route["decision"] != "INCLUDED" or trigger in answered:
                    continue
                rank = route["rank"] if route["rank"] is not None else UNRANKED
                item = TodayItem(
                    f"event-{trigger}",
                    BREAKING_UNVERIFIED,
                    entry["symbol"],
                    "EVENT",
                    HEADLINE[BREAKING_UNVERIFIED],
                    route["first_seen_at"],
                    None,
                    None,
                    UNKNOWN[BREAKING_UNVERIFIED],
                )
                found[str(trigger)] = ((rank, route["first_seen_at"], str(trigger)), item)
    return [item for _, item in sorted(found.values(), key=lambda pair: pair[0])]


def updates(records: Sequence[Mapping[str, Any]]) -> list[TodayItem]:
    """One item per thesis revision that says a premise changed (``post_snapshot``): the recorded call stays as it was, the item says what
    changed. The newest event first."""
    symbols = {r["id"]: r["body"]["symbol"] for r in records if r.get("type") == "forecast"}
    seen = {r["id"]: r["body"]["first_seen_at"] for r in records if r.get("type") == "post_snapshot_event"}
    found = []
    for record in records:
        if record.get("type") != "thesis_revision" or record["body"]["kind"] != "MATERIAL_UPDATE":
            continue
        body = record["body"]
        thesis, event = body["thesis_id"], body["event_id"]
        if thesis not in symbols or event not in seen:
            raise ItemError("a revision names a thesis or an event that the ledger does not hold")
        item = TodayItem(
            f"update-{record['id']}",
            MATERIAL_UPDATE,
            symbols[thesis],
            "EVENT",
            HEADLINE[MATERIAL_UPDATE],
            seen[event],
            body["changed"][0],
            thesis,
            UNKNOWN[MATERIAL_UPDATE],
        )
        found.append(item)
    return sorted(found, key=lambda i: (i.first_seen or "", i.item_id), reverse=True)


def items(records: Sequence[Mapping[str, Any]], runs: Sequence[desk_view.DeskRun]) -> tuple[TodayItem, ...]:
    """Every item of the developments list, in the printed order. ``records`` are the ledger's. The order is the provisional
    research-impact ranking (``impact``); items of equal score keep the order they are collected in: updates, then runs by state in
    the order they were recorded, then the unchecked in their manifest's order."""
    from hoshitori import impact  # imported here: the ranking reads this module's constants

    from_runs = [_run_item(run) for run in runs]
    rank = {state: n for n, state in enumerate(STATES)}
    ordered = sorted(from_runs, key=lambda i: rank[i.state])  # a stable sort: runs keep the order they were recorded in
    return impact.slate((*updates(records), *ordered, *unchecked(records, runs)))
