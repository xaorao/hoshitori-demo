"""Checks of the developments list of Today (``hoshitori.today_items``), runnable against a mutant."""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Callable
from typing import Any

from hoshitori import desk_view

COUNTS = desk_view.Counts(0, 0, 0)
ORIGINS = desk_view.Origins((), (), (), 0)


def run(
    mod: types.ModuleType,
    run_id: str,
    symbol: str,
    state: str,
    *,
    open_: bool = False,
    route: str = "EVENT",
    reason: str | None = None,
    label: str = "live",
    trigger: str | None = None,
) -> desk_view.DeskRun:
    return desk_view.DeskRun(
        run_id, label, symbol, route, state, reason, None, open_, None, (), ORIGINS, None, None, COUNTS, COUNTS, trigger
    )


def manifest(*events: tuple[Any, ...], route: str = "EVENT") -> dict[str, Any]:
    """A sealed manifest whose entries are (symbol, trigger id, first seen, decision[, rank]) routes; the rank defaults to the position."""
    entries = []
    for position, event in enumerate(events, start=1):
        symbol, trigger, seen, decision = event[:4]
        rank = event[4] if len(event) > 4 else position
        entries.append(
            {
                "symbol": symbol,
                "routes": [{"route": route, "decision": decision, "trigger_id": trigger, "first_seen_at": seen, "rank": rank}],
            }
        )
    return {"type": "manifest", "body": {"entries": entries}}


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def raises(self, label: str, call: Callable[[], object], error: type[BaseException]) -> None:
        try:
            call()
        except error:
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}, expected {error.__name__}")
            return
        self.bad.append(f"{label}: raised nothing")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "states",
        (mod.BREAKING_UNVERIFIED, mod.BEING_CHECKED, mod.NO_RELIABLE_CONCLUSION, mod.INVESTIGATED_THESIS),
        ("BREAKING_UNVERIFIED", "BEING_CHECKED", "NO_RELIABLE_CONCLUSION", "INVESTIGATED_THESIS"),
    )
    c.eq(
        "the order of the states",
        mod.STATES,
        (mod.INVESTIGATED_THESIS, mod.BEING_CHECKED, mod.NO_RELIABLE_CONCLUSION, mod.BREAKING_UNVERIFIED),
    )
    c.eq(
        "the rule",
        mod.RULE,
        "Ordered by a provisional research-impact score (impact/1), not by time: the highest-scoring investigated or updated call first, "
        "the highest-scoring development not yet checked directly beneath it, then the rest by score, with at most two of the top five "
        "about one token unless an item scores high. Equal scores keep the order they arrived in. The score is a sum of printed parts; a "
        "part the product cannot compute is left out, never counted as zero. Provisional research-impact ranking; independent validation "
        "pending.",
    )
    c.eq(
        "what is unknown",
        mod.UNKNOWN,
        {
            "BREAKING_UNVERIFIED": "Nothing about this has been checked.",
            "BEING_CHECKED": "The checks are still running.",
            "NO_RELIABLE_CONCLUSION": "The checks did not reach a reliable conclusion.",
            "INVESTIGATED_THESIS": "The thesis is a practice call, not a statement that it is true.",
            "MATERIAL_UPDATE": "The recorded call is unchanged; the part of it that settles it may no longer be available.",
        },
    )
    c.eq(
        "the headlines",
        mod.HEADLINE,
        {
            "BREAKING_UNVERIFIED": "A listing changed on a venue.",
            "BEING_CHECKED": "The Desk is checking a move.",
            "NO_RELIABLE_CONCLUSION": "The Desk checked a move and reached no reliable conclusion.",
            "INVESTIGATED_THESIS": "The Desk checked a move and recorded a practice call.",
            "MATERIAL_UPDATE": "A premise of a recorded call changed after it was made.",
        },
    )
    return c.bad


def check_run_states(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    cases = (
        ("COMMITTED", False, "INVESTIGATED_THESIS"),
        ("RUNNING", True, "BEING_CHECKED"),
        ("ABSTAINED", False, "NO_RELIABLE_CONCLUSION"),
        ("FAILED", False, "NO_RELIABLE_CONCLUSION"),
        ("FAILED_FINAL", False, "NO_RELIABLE_CONCLUSION"),
    )
    for state, is_open, want in cases:
        listed = mod.items([], [run(mod, "r1", "SOL", state, open_=is_open, reason="WHY", label="recorded")])
        c.eq(f"{state}: one item", len(listed), 1)
        if listed:
            item = listed[0]
            c.eq(
                f"{state}: the item",
                (
                    item.item_id,
                    item.state,
                    item.symbol,
                    item.route,
                    item.headline,
                    item.first_seen,
                    item.reason,
                    item.link,
                    item.unknown,
                    item.label,
                ),
                ("run-r1", want, "SOL", "EVENT", mod.HEADLINE[want], None, "WHY", "r1", mod.UNKNOWN[want], "recorded"),
            )
    frozen = mod.items([], [run(mod, "r1", "SOL", "COMMITTED")])[0]
    c.raises("an item cannot be changed once made", lambda: setattr(frozen, "state", "BEING_CHECKED"), dataclasses.FrozenInstanceError)
    c.eq(
        "an open run is being checked whatever its last state says",
        mod.items([], [run(mod, "r", "A", "COMMITTED", open_=True)])[0].state,
        "BEING_CHECKED",
    )
    c.raises("an unknown state is refused", lambda: mod.items([], [run(mod, "r", "A", "SOMETHING_ELSE")]), mod.ItemError)
    return c.bad


def check_unchecked(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    listed = mod.items([manifest(("DOGE", "t9", "2026-11-06T10:00:00Z", "INCLUDED"))], [])
    c.eq("one unchecked item", len(listed), 1)
    if listed:
        i = listed[0]
        c.eq(
            "the unchecked item",
            (i.item_id, i.state, i.symbol, i.route, i.headline, i.first_seen, i.reason, i.link, i.unknown, i.label),
            (
                "event-t9",
                "BREAKING_UNVERIFIED",
                "DOGE",
                "EVENT",
                mod.HEADLINE["BREAKING_UNVERIFIED"],
                "2026-11-06T10:00:00Z",
                None,
                None,
                mod.UNKNOWN["BREAKING_UNVERIFIED"],
                None,
            ),
        )
    c.eq("an excluded event makes no item", mod.items([manifest(("A", "t", "2026-11-06T10:00:00Z", "EXCLUDED"))], []), ())
    c.eq("another route makes no item", mod.items([manifest(("A", "t", "2026-11-06T10:00:00Z", "INCLUDED"), route="RYO_SCAN")], []), ())
    other = {
        "type": "settlement",
        "body": {
            "entries": [
                {"symbol": "X", "routes": [{"route": "EVENT", "decision": "INCLUDED", "trigger_id": "t", "first_seen_at": "x", "rank": 1}]}
            ]
        },
    }
    c.eq("another record type makes no item", mod.items([other], []), ())
    answered = mod.items([manifest(("SOL", "t1", "2026-11-06T10:00:00Z", "INCLUDED"))], [run(mod, "r1", "SOL", "COMMITTED", trigger="t1")])
    c.eq("an event answered by the run of its trigger is one item", [i.state for i in answered], ["INVESTIGATED_THESIS"])
    two_events = manifest(("SOL", "tA", "2026-11-06T10:00:00Z", "INCLUDED"), ("SOL", "tB", "2026-11-06T11:00:00Z", "INCLUDED"))
    one_answered = mod.items([two_events], [run(mod, "r1", "SOL", "COMMITTED", trigger="tA")])
    c.eq(
        "a run answers its own trigger and not another event of the same token",
        [(i.item_id, i.state) for i in one_answered],
        [("run-r1", "INVESTIGATED_THESIS"), ("event-tB", "BREAKING_UNVERIFIED")],
    )
    no_trigger = mod.items([two_events], [run(mod, "r1", "SOL", "COMMITTED")])
    c.eq(
        "a run that started from no trigger answers none",
        sorted(i.state for i in no_trigger),
        ["BREAKING_UNVERIFIED"] * 2 + ["INVESTIGATED_THESIS"],
    )
    other_token = mod.items(
        [manifest(("SOL", "t1", "2026-11-06T10:00:00Z", "INCLUDED"))], [run(mod, "r1", "ETH", "COMMITTED", trigger="t9")]
    )
    c.eq(
        "a run of another trigger does not answer it", sorted(i.state for i in other_token), ["BREAKING_UNVERIFIED", "INVESTIGATED_THESIS"]
    )
    ranked = mod.items(
        [
            manifest(
                ("D", "t1", "2026-11-06T09:00:00Z", "INCLUDED", 3),
                ("E", "t2", "2026-11-06T11:00:00Z", "INCLUDED", 1),
                ("F", "t3", "2026-11-06T10:00:00Z", "INCLUDED", 2),
            )
        ],
        [],
    )
    c.eq("the unchecked come in the manifest's rank order", [i.symbol for i in ranked], ["E", "F", "D"])
    ties = mod.items(
        [manifest(("D", "t1", "2026-11-06T11:00:00Z", "INCLUDED", 1), ("E", "t2", "2026-11-06T09:00:00Z", "INCLUDED", 1))],
        [],
    )
    c.eq("equal ranks fall back to the earlier first seen", [i.symbol for i in ties], ["E", "D"])
    unranked = mod.items(
        [manifest(("D", "t1", "2026-11-06T08:00:00Z", "INCLUDED", None), ("E", "t2", "2026-11-06T12:00:00Z", "INCLUDED", 7))],
        [],
    )
    c.eq("an event route with no rank sorts after every ranked one", [i.symbol for i in unranked], ["E", "D"])
    edge = mod.items(
        [manifest(("A", "t1", "2026-11-06T12:00:00Z", "INCLUDED", 10**9), ("B", "t2", "2026-11-06T08:00:00Z", "INCLUDED", None))],
        [],
    )
    c.eq("no rank counts as a rank of a thousand million", [i.symbol for i in edge], ["B", "A"])
    same = mod.items([manifest(("D", "t1", "2026-11-06T09:00:00Z", "INCLUDED"), ("D", "t1", "2026-11-06T09:00:00Z", "INCLUDED"))], [])
    c.eq("one trigger seen twice is one item", len(same), 1)
    two = mod.items([manifest(("D", "t1", "2026-11-06T09:00:00Z", "INCLUDED"), ("D", "t2", "2026-11-06T10:00:00Z", "INCLUDED"))], [])
    c.eq("two triggers of one symbol are two items", [i.item_id for i in two], ["event-t1", "event-t2"])
    return c.bad


def ledger_records() -> list[dict[str, Any]]:
    """Two forecasts, one event, a material revision of the first and a non-material one of the second."""
    return [
        {"type": "forecast", "id": "fc-1", "body": {"symbol": "BTC"}},
        {"type": "forecast", "id": "fc-2", "body": {"symbol": "ETH"}},
        {"type": "post_snapshot_event", "id": "pe-1", "body": {"first_seen_at": "2026-11-06T12:00:00.000Z"}},
        {"type": "post_snapshot_event", "id": "pe-2", "body": {"first_seen_at": "2026-11-06T13:00:00.000Z"}},
        {
            "type": "thesis_revision",
            "id": "tr-1",
            "body": {"thesis_id": "fc-1", "event_id": "pe-1", "kind": "MATERIAL_UPDATE", "changed": ["ORACLE_INSTRUMENT_AVAILABLE"]},
        },
        {
            "type": "thesis_revision",
            "id": "tr-2",
            "body": {"thesis_id": "fc-2", "event_id": "pe-1", "kind": "NO_MATERIAL_CHANGE", "changed": []},
        },
        {
            "type": "thesis_revision",
            "id": "tr-3",
            "body": {"thesis_id": "fc-2", "event_id": "pe-2", "kind": "MATERIAL_UPDATE", "changed": ["ORACLE_INSTRUMENT_AVAILABLE"]},
        },
    ]


def check_updates(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    got = mod.items(ledger_records(), [])
    c.eq(
        "only material revisions are items, the newest event first",
        [(i.item_id, i.symbol, i.first_seen) for i in got],
        [("update-tr-3", "ETH", "2026-11-06T13:00:00.000Z"), ("update-tr-1", "BTC", "2026-11-06T12:00:00.000Z")],
    )
    first = got[1]
    c.eq(
        "an update item",
        (first.state, first.route, first.headline, first.reason, first.link, first.unknown, first.label),
        (
            "MATERIAL_UPDATE",
            "EVENT",
            mod.HEADLINE["MATERIAL_UPDATE"],
            "ORACLE_INSTRUMENT_AVAILABLE",
            "fc-1",
            mod.UNKNOWN["MATERIAL_UPDATE"],
            None,
        ),
    )
    broken = [r for r in ledger_records() if r["id"] != "fc-1"]
    c.raises("a revision of a thesis the ledger does not hold is an error", lambda: mod.items(broken, []), mod.ItemError)
    no_event = [r for r in ledger_records() if r["id"] != "pe-2"]
    c.raises("a revision of an event the ledger does not hold is an error", lambda: mod.items(no_event, []), mod.ItemError)
    both = mod.items(
        [*ledger_records(), manifest(("DOGE", "t1", "2026-11-06T10:00:00Z", "INCLUDED"))],
        [run(mod, "r1", "SOL", "COMMITTED"), run(mod, "r2", "XRP", "ABSTAINED")],
    )
    c.eq(
        "the highest-scoring update is featured, the unchecked one is reserved, the other update outranks the unscored runs",
        [i.symbol for i in both],
        ["ETH", "DOGE", "BTC", "SOL", "XRP"],
    )
    only = mod.items(ledger_records(), [run(mod, "r2", "XRP", "ABSTAINED")])
    c.eq("without a featured thesis the updates come first", [i.symbol for i in only], ["ETH", "BTC", "XRP"])
    c.eq("a record that is not a revision is no item", mod.items([{"type": "forecast", "id": "x", "body": {"symbol": "A"}}], []), ())
    return c.bad


def check_defaults(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    bare = mod.TodayItem("i", "BREAKING_UNVERIFIED", "BTC", "EVENT", "h", None, None, None, "u")
    c.eq(
        "an item starts unscored, with no counts and no reasons",
        (bare.label, bare.contradicting, bare.origins_read, bare.score, bare.reasons),
        (None, 0, 0, 0, ()),
    )
    return c.bad


def check_order(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    runs = [
        run(mod, "a", "A", "ABSTAINED"),
        run(mod, "b", "B", "RUNNING", open_=True),
        run(mod, "c", "C", "COMMITTED"),
        run(mod, "d", "D", "COMMITTED"),
    ]
    events = manifest(("X", "t1", "2026-11-06T09:00:00Z", "INCLUDED"), ("Y", "t2", "2026-11-06T11:00:00Z", "INCLUDED"))
    c.eq(
        "the best unchecked item is the reserved row beneath the featured thesis, the rest follow by score",
        [i.symbol for i in mod.items([events], runs)],
        ["C", "X", "Y", "D", "B", "A"],
    )
    c.eq("without an unchecked one, runs by state", [i.symbol for i in mod.items([], runs)], ["C", "D", "B", "A"])
    none = [run(mod, "a", "A", "ABSTAINED"), run(mod, "b", "B", "RUNNING", open_=True)]
    c.eq("without a thesis the unchecked still outrank unscored runs", [i.symbol for i in mod.items([events], none)], ["X", "Y", "B", "A"])
    c.eq("nothing in, nothing out", mod.items([], []), ())
    scored = mod.items([events], runs)
    c.eq("items come back scored", [(i.symbol, i.score) for i in scored][:3], [("C", 0), ("X", 3), ("Y", 3)])
    c.eq(
        "with reasons",
        [i.reasons for i in scored][:3],
        [(), ("Nothing about it has been checked yet.",), ("Nothing about it has been checked yet.",)],
    )
    c.eq(
        "runs keep their recorded order within a state",
        [i.symbol for i in mod.items([], [run(mod, "z", "Z", "ABSTAINED"), run(mod, "a", "A", "ABSTAINED")])],
        ["Z", "A"],
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_run_states, check_unchecked, check_updates, check_defaults, check_order):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
