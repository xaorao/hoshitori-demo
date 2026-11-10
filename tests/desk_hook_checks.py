"""Scenarios for the Desk inside the league (``hoshitori.desk_hook``), runnable against the real module or a mutant of it.

Every expected value is worked out by hand from the rule in the module's docstring: an included candidate of the ``EVENT`` route whose
event was seen at most a day before the snapshot, a move ``|ret_h| / (atr14_share * sqrt(h))`` over the shortest of 1, 4 or 24 hours that
covers the event, at least 2; at most three runs start in a UTC day, a token once a day, a trigger never twice.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

DAY = 86_400_000
HOUR = 3_600_000
NOON = 1_794_225_600_000  # 2026-11-09T12:00:00Z, a whole number of days plus twelve hours
assert NOON % DAY == DAY // 2
NOON_TEXT = "2026-11-09T12:00:00.000Z"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def evidence(ident: str, source: str, **fields: str) -> dict[str, Any]:
    return {"type": "evidence", "id": ident, "body": {"source": source, "fields": dict(fields)}}


def stamp(ms: int) -> str:
    from hoshitori.timeline import from_ms
    from hoshitori.timeutil import format_rfc3339_ms

    return format_rfc3339_ms(from_ms(ms))


def cycle(cycle_id: str, tokens: list[dict[str, Any]], at: object = NOON_TEXT) -> list[dict[str, Any]]:
    """A snapshot, a manifest and evidence for each token: ``symbol``; ``fields`` of its features evidence (none: only other evidence);
    ``seen_ms`` when its event was first seen (none: it came in on the watchlist); ``decision`` of the event route; ``inclusion``."""
    records: list[dict[str, Any]] = []
    candidates, entries = [], []
    for t in tokens:
        symbol = t["symbol"]
        cand, asset = f"cd-{cycle_id}-{symbol}", f"as-{symbol}"
        items = [{"evidence_id": f"ev-{cycle_id}-{symbol}-other"}]
        records.append(evidence(f"ev-{cycle_id}-{symbol}-other", "ryo.analyze_token", ret_1h="0.5", ret_4h="0.5", atr14_share="0.01"))
        if t.get("fields") is not None:
            items.append({"evidence_id": f"ev-{cycle_id}-{symbol}-f"})
            records.append(evidence(f"ev-{cycle_id}-{symbol}-f", "hoshitori.features", **t["fields"]))
        candidates.append({"candidate_id": cand, "asset_id": asset, "items": items})
        watch = {"route": "WATCHLIST", "decision": "INCLUDED", "trigger_id": None, "first_seen_at": None}
        routes = [watch]
        if t.get("seen_ms") is not None:
            event = {
                "route": "EVENT",
                "decision": t.get("decision", "INCLUDED"),
                "trigger_id": f"tg-{symbol}",
                "first_seen_at": stamp(t["seen_ms"]),
            }
            routes = [event] if not t.get("also_watched") else [watch, event]
        entries.append({"candidate_id": cand, "symbol": symbol, "inclusion": t.get("inclusion", "INCLUDED"), "routes": routes})
    records.append(
        {"type": "snapshot", "id": f"sn-{cycle_id}", "body": {"cycle_id": cycle_id, "snapshot_at": at, "candidates": candidates}}
    )
    records.append({"type": "manifest", "id": f"mf-{cycle_id}", "body": {"cycle_id": cycle_id, "entries": entries}})
    return records


def run_state(run_id: str, symbol: str, at_ms: int, trigger: str | None, state: str = "RUNNING") -> dict[str, Any]:
    return {"type": "desk_run_state", "body": {"run_id": run_id, "symbol": symbol, "at_ms": at_ms, "trigger_id": trigger, "state": state}}


def f(ret1: str = "0", ret4: str = "0", ret24: str = "0", share: str = "0.01") -> dict[str, str]:
    return {"ret_1h": ret1, "ret_4h": ret4, "ret_24h": ret24, "atr14_share": share}


def check_moves(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    league = cycle(
        "c1",
        [
            {"symbol": "BTC", "fields": f(ret1="0.02"), "seen_ms": NOON - 30 * 60_000},  # 30 minutes: the hour; 0.02 / 0.01 = 2, a move
            {"symbol": "ETH", "fields": f(ret1="0.5", ret4="-0.039"), "seen_ms": NOON - HOUR - 1},  # past the hour: 4 h, 0.039/0.02 = 1.95
            {"symbol": "SOL", "fields": f(ret1="0.5", ret4="-0.06"), "seen_ms": NOON - 4 * HOUR},  # exactly four hours: 4 h, 3, down
            {
                "symbol": "ADA",
                "fields": f(ret4="n/a"),
                "seen_ms": NOON - 2 * HOUR,
            },  # after a move of 3: a skipped bad number must not reuse SOL's
            {"symbol": "XRP", "fields": {"atr14_share": "0.01"}, "seen_ms": NOON - 2 * HOUR},  # no return at all: never a trigger
            {"symbol": "DOT", "fields": f(ret4="0.5", ret24="0.098"), "seen_ms": NOON - 4 * HOUR - 1},  # 24 h: 0.098/(0.01*4.899) = 2.0004
            {"symbol": "LTC", "fields": f(ret24="0.0979"), "seen_ms": NOON - DAY},  # exactly a day: 24 h, 1.998, not a move
            {"symbol": "DOGE", "fields": f(ret1="0.9", share="0"), "seen_ms": NOON},  # seen at the snapshot; no expected move: never
            {"symbol": "OLD", "fields": f(ret24="0.9"), "seen_ms": NOON - DAY - 1},  # more than a day before: no window covers it
            {"symbol": "LATE", "fields": f(ret1="0.9"), "seen_ms": NOON + 1},  # seen after the snapshot: no window covers it
            {"symbol": "WATCH", "fields": f(ret1="0.9")},  # watchlist only: no event, never a trigger
            {"symbol": "OUT", "fields": f(ret1="0.9"), "seen_ms": NOON - 60_000, "decision": "EXCLUDED"},  # the event route excluded it
            {"symbol": "GONE", "fields": f(ret1="0.9"), "seen_ms": NOON - 60_000, "inclusion": "EXCLUDED"},  # the candidate excluded
            {"symbol": "BOTH", "fields": f(ret1="-0.03"), "seen_ms": NOON - 60_000, "also_watched": True},  # watched and an event: 3, down
            {"symbol": "AVAX", "seen_ms": NOON - 60_000},  # a large number in other evidence only: never read
        ],
    )
    league += cycle("c2", [{"symbol": "LINK", "fields": f(ret1="0.1"), "seen_ms": NOON - 60_000}])  # another cycle: not this one's
    got = mod.moves(league, "c1")
    c.eq(
        "the moves, in the snapshot's order",
        [(m.symbol, m.hours, round(m.size, 4), m.direction) for m in got],
        [
            ("BTC", 1, Decimal(2), "UP"),
            ("SOL", 4, Decimal(3), "DOWN"),
            ("DOT", 24, Decimal("2.0004"), "UP"),
            ("BOTH", 1, Decimal(3), "DOWN"),
        ],
    )
    c.eq("the 24-hour size is exact", got[2].size if len(got) > 2 else None, Decimal("0.098") / (Decimal("0.01") * Decimal(24).sqrt()))
    c.eq("the cycle, asset and event", [(m.cycle_id, m.asset_id, m.event_id) for m in got[:1]], [("c1", "as-BTC", "tg-BTC")])
    c.eq("the event's time", [m.event_ms for m in got], [NOON - 30 * 60_000, NOON - 4 * HOUR, NOON - 4 * HOUR - 1, NOON - 60_000])
    c.eq("a cycle with no snapshot has no moves", mod.moves(league, "c9"), ())
    c.eq(
        "the rule",
        (mod.RULE, mod.THRESHOLD, mod.WINDOWS, mod.DAILY_RUNS, mod.RYO_CALLS),
        ("EVENT_THEN_MOVE_2_EXPECTED", Decimal(2), (1, 4, 24), 3, 1),
    )
    no_manifest = [r for r in cycle("c3", [{"symbol": "BTC", "fields": f(ret1="0.1"), "seen_ms": NOON}]) if r["type"] != "manifest"]
    c.eq("a cycle with no manifest has no moves", mod.moves(no_manifest, "c3"), ())
    for label, at in (("a snapshot without a time", None), ("a snapshot whose time is true or false", True)):
        try:
            mod.moves(cycle("c4", [{"symbol": "BTC", "fields": f(ret1="0.1"), "seen_ms": NOON}], at=at), "c4")
            c.bad.append(f"{label} was read")
        except mod.DeskHookError:
            pass
    whole = mod.moves(cycle("c5", [{"symbol": "BTC", "fields": f(ret1="0.1"), "seen_ms": NOON}], at=NOON), "c5")
    c.eq("a whole-number time is taken as it is", [m.hours for m in whole], [1])
    return c.bad


def call(cycle_id: str, symbol: str, arm: str, p: str, hours: int = 1, contract: str = "D") -> dict[str, Any]:
    body: dict[str, Any] = {
        "cycle_id": cycle_id,
        "arm_id": arm,
        "contract": contract,
        "asset_id": f"as-{symbol}",
        "symbol": symbol,
        "horizon_ms": hours * HOUR,
    }
    if contract == "D":
        body["p"] = p
    return {"type": "forecast", "id": f"fc-{cycle_id}-{symbol}-{arm}-{hours}", "body": body}


def check_disputes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    league = cycle(
        "c1",
        [
            {"symbol": "BTC", "fields": f()},
            {"symbol": "ETH", "fields": f(), "seen_ms": NOON - 60_000},
            {"symbol": "SOL", "fields": f()},
            {"symbol": "ADA", "fields": f()},
            {"symbol": "XRP", "fields": f()},
            {"symbol": "GONE", "fields": f(), "inclusion": "EXCLUDED"},
            {"symbol": "DOT", "fields": f()},
            {"symbol": "LTC", "fields": f()},
        ],
    )
    league += [
        call("c1", "BTC", "L0", "0.43"),
        call("c1", "BTC", "L1", "0.64"),
        call("c1", "BTC", "L2", "0.50"),  # 0.21 apart, on opposite sides
        call("c1", "ETH", "L0", "0.49", 4),
        call("c1", "ETH", "L1", "0.55", 4),  # exactly 0.06 and 0.49 / 0.55: the edge, inside
        call("c1", "SOL", "L0", "0.49"),
        call("c1", "SOL", "L1", "0.54"),  # 0.05 apart: under the gap
        call("c1", "ADA", "L0", "0.50"),
        call("c1", "ADA", "L1", "0.70"),  # the lower is not under 0.49 (a coin flip is no side)
        call("c1", "XRP", "L0", "0.52"),
        call("c1", "XRP", "L1", "0.70"),  # both above 0.5
        call("c1", "GONE", "L0", "0.10"),
        call("c1", "GONE", "L1", "0.90"),  # the candidate was excluded
        call("c1", "LTC", "L0", "0.45"),
        call("c1", "LTC", "L1", "0.51"),  # the higher is exactly 0.51 and the gap exactly 0.06: inside
        call("c1", "DOT", "L0", "0.30", 24),
        call("c1", "DOT", "L1", "0.60", 24),  # a day
        call("c1", "DOT", "L0", "0.30", 1),
        call("c1", "DOT", "L1", "0.30", 1),  # a different horizon of the same token: no pair across
        call("c1", "BTC", "L0", "x", 1, "B"),
        call("c1", "BTC", "L1", "x", 1, "B"),  # barrier forecasts take no side
        call("c1", "NOPE", "L0", "0.1"),
        call("c1", "NOPE", "L1", "0.9"),  # not in the manifest
        call("c2", "SOL", "L0", "0.10"),
        call("c2", "SOL", "L1", "0.90"),  # another cycle
        {
            "type": "forecast",
            "id": "fc-bad",
            "body": {
                "cycle_id": "c1",
                "arm_id": "L9",
                "contract": "D",
                "asset_id": "as-SOL",
                "symbol": "SOL",
                "horizon_ms": HOUR,
                "p": "n/a",
            },
        },  # an unreadable chance takes no side
    ]
    got = mod.disputes(league, "c1")
    c.eq(
        "the disputes, by token then window",
        [(d.symbol, d.hours, d.gap, d.low_arm, d.high_arm) for d in got],
        [
            ("BTC", 1, Decimal("0.21"), "L0", "L1"),
            ("DOT", 24, Decimal("0.30"), "L0", "L1"),
            ("ETH", 4, Decimal("0.06"), "L0", "L1"),
            ("LTC", 1, Decimal("0.06"), "L0", "L1"),
        ],
    )
    c.eq(
        "the route of each",
        [(d.symbol, d.route) for d in got],
        [("BTC", "WATCHLIST"), ("DOT", "WATCHLIST"), ("ETH", "EVENT"), ("LTC", "WATCHLIST")],
    )
    c.eq("the cycle and the asset", [(d.cycle_id, d.asset_id) for d in got[:1]], [("c1", "as-BTC")])
    c.eq("a cycle with no manifest has no disputes", mod.disputes(league, "c9"), ())
    c.eq("another cycle's own", [(d.symbol, d.gap) for d in mod.disputes(league, "c2")], [])
    c.eq(
        "the rule",
        (mod.RULE_DISPUTE, mod.LOW_MAX, mod.HIGH_MIN, mod.GAP, mod.MAX_HOURS),
        ("AGENTS_DISAGREE_6_POINTS", 49, 51, 6, 24),
    )
    rounded = cycle("c3", [{"symbol": "BTC", "fields": f()}, {"symbol": "ETH", "fields": f()}, {"symbol": "SOL", "fields": f()}])
    rounded += [
        call("c3", "BTC", "L0", "0.494"),
        call("c3", "BTC", "L1", "0.555"),  # 49.4 rounds to 49 and 55.5 to 56: seven points, a dispute
        call("c3", "ETH", "L0", "0.4949"),
        call("c3", "ETH", "L1", "0.5449"),  # 49 and 54: five points, none
        call("c3", "SOL", "L0", "0.495"),
        call("c3", "SOL", "L1", "0.505"),  # 50 and 51: the lower is a coin flip (a half rounds up)
    ]
    c.eq(
        "chances are rounded to whole percents, a half up, as the page shows them",
        [(d.symbol, d.gap) for d in mod.disputes(rounded, "c3")],
        [("BTC", Decimal("0.07"))],
    )
    long_window = [
        *cycle("c4", [{"symbol": "BTC", "fields": f()}]),
        call("c4", "BTC", "L0", "0.10", 48),
        call("c4", "BTC", "L1", "0.90", 48),
    ]
    c.eq("a window over 24 hours is no dispute", mod.disputes(long_window, "c4"), ())
    day = [*cycle("c5", [{"symbol": "BTC", "fields": f()}]), call("c5", "BTC", "L0", "0.10", 24), call("c5", "BTC", "L1", "0.90", 24)]
    c.eq("24 hours is allowed", [d.hours for d in mod.disputes(day, "c5")], [24])
    odd = [call("c1", "BTC", "L0", "0.10", 1), call("c1", "BTC", "L1", "0.90", 1)]
    odd[0]["body"]["horizon_ms"] = HOUR + 1
    c.eq("a horizon that is not whole hours is no dispute", mod.disputes(cycle("c1", [{"symbol": "BTC", "fields": f()}]) + odd, "c1"), ())
    return c.bad


def dispute(mod: types.ModuleType, symbol: str, gap: str, hours: int = 1, cycle_id: str = "c1") -> Any:
    return mod.Dispute(cycle_id, f"as-{symbol}", symbol, "WATCHLIST", hours, Decimal(gap), "L0", "L1")


def check_pick_disputes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    none = mod.Today(0, frozenset(), frozenset())
    moved = [move(mod, "ETH", "3")]
    disputed = [dispute(mod, "BTC", "0.10"), dispute(mod, "SOL", "0.30"), dispute(mod, "ADA", "0.30")]
    picked = mod.pick(moved, none, disputed)
    c.eq("moves first, then the widest disagreements, a tie by symbol", [t.symbol for t in picked], ["ETH", "ADA", "SOL"])
    t = picked[1]
    c.eq("a disagreement trigger", (t.kind, t.route, t.direction, t.hours, t.event_time_ms), ("DISAGREEMENT", "WATCHLIST", "UP", 1, None))
    c.eq("a move trigger keeps its kind", picked[0].kind, "MOVE")
    c.eq("no disputes given: as before", [x.symbol for x in mod.pick(moved, none)], ["ETH"])
    c.eq("the cap is shared", [x.symbol for x in mod.pick(moved, mod.Today(2, frozenset(), frozenset()), disputed)], ["ETH"])
    c.eq("no room for disagreements", mod.pick([], mod.Today(3, frozenset(), frozenset()), disputed), ())
    c.eq(
        "one run per token: a move on the token takes the day",
        [x.symbol for x in mod.pick(moved, none, [dispute(mod, "ETH", "0.5")])],
        ["ETH"],
    )
    c.eq("and its own kind is the move's", mod.pick(moved, none, [dispute(mod, "ETH", "0.5")])[0].kind, "MOVE")
    c.eq(
        "a token seen today is skipped",
        [x.symbol for x in mod.pick([], mod.Today(1, frozenset({"SOL"}), frozenset()), disputed)],
        ["ADA", "BTC"],
    )
    ident = mod.trigger_of_dispute(disputed[0]).trigger_id
    c.eq(
        "a trigger already run is skipped",
        [x.symbol for x in mod.pick([], mod.Today(0, frozenset(), frozenset({ident})), disputed)],
        ["ADA", "SOL"],
    )
    c.eq("the id is the cycle, token, window and rule (a pinned regression value)", ident, "dtmsl5rfq5i6gwov54byecjtcgme")
    c.eq("and not the gap", ident, mod.trigger_of_dispute(dispute(mod, "BTC", "0.99")).trigger_id)
    c.eq(
        "another cycle, token or window is another trigger",
        len(
            {
                mod.trigger_of_dispute(d).trigger_id
                for d in (
                    dispute(mod, "BTC", "0.1"),
                    dispute(mod, "BTC", "0.1", cycle_id="c2"),
                    dispute(mod, "ETH", "0.1"),
                    dispute(mod, "BTC", "0.1", 4),
                )
            }
        ),
        4,
    )
    c.eq(
        "a disagreement is not the move's trigger",
        mod.trigger_of_dispute(dispute(mod, "ETH", "0.1")).trigger_id != mod.trigger_of(move(mod, "ETH", "3")).trigger_id,
        True,
    )
    return c.bad


def check_today(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    start = NOON - DAY // 2
    desk = [
        run_state("r1", "BTC", start, "t1"),
        run_state("r1", "BTC", start + 5, "t1", "COMMITTED"),  # the same run's end: one run, not two
        run_state("r2", "ETH", start + DAY - 1, "t2"),  # the last millisecond of today
        run_state("r3", "SOL", start - 1, "t3"),  # yesterday: not today's, but its trigger is never run again
        run_state("r4", "ADA", start + DAY, None),  # tomorrow; a hand-run with no trigger
        {"type": "decision", "body": {"run_id": "r5"}},
    ]
    seen = mod.today(desk, NOON)
    c.eq("runs today", seen.runs, 2)
    c.eq("tokens today", seen.symbols, frozenset({"BTC", "ETH"}))
    c.eq("every trigger ever run", seen.triggers, frozenset({"t1", "t2", "t3"}))
    c.eq("an empty ledger", mod.today([], NOON), mod.Today(0, frozenset(), frozenset()))
    return c.bad


def move(mod: types.ModuleType, symbol: str, size: str, event: str | None = None, hours: int = 4) -> Any:
    return mod.Move("c1", f"as-{symbol}", symbol, event or f"tg-{symbol}", NOON - HOUR, hours, Decimal(size), "UP")


def check_pick(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    found = [move(mod, "BTC", "2.5"), move(mod, "ETH", "4"), move(mod, "SOL", "3"), move(mod, "DOGE", "2.1")]
    none = mod.Today(0, frozenset(), frozenset())
    picked = mod.pick(found, none)
    c.eq("the largest three", [t.symbol for t in picked], ["ETH", "SOL", "BTC"])
    t = picked[0]
    c.eq(
        "the trigger's route, direction, window and event time",
        (t.route, t.direction, t.hours, t.event_time_ms),
        ("EVENT", "UP", 4, NOON - HOUR),
    )
    c.eq("the trigger's id is the event and the rule", t.trigger_id, "dtbevnh3s6xuwy7mixwli7kbtrgx")  # natural_id of tg-ETH and the rule
    c.eq("another event is another trigger", mod.trigger_of(move(mod, "ETH", "4", "tg-other")).trigger_id != t.trigger_id, True)
    c.eq("the window is the move's", mod.trigger_of(move(mod, "ETH", "4", hours=24)).hours, 24)
    c.eq("a tie goes by symbol", [x.symbol for x in mod.pick([move(mod, "SOL", "3"), move(mod, "ADA", "3")], none)], ["ADA", "SOL"])
    c.eq("room for one", [x.symbol for x in mod.pick(found, mod.Today(2, frozenset(), frozenset()))], ["ETH"])
    c.eq("no room", mod.pick(found, mod.Today(3, frozenset(), frozenset())), ())
    c.eq("more than the cap already run", mod.pick(found, mod.Today(5, frozenset(), frozenset())), ())
    c.eq(
        "a token seen today is skipped, not counted",
        [x.symbol for x in mod.pick(found, mod.Today(1, frozenset({"ETH"}), frozenset()))],
        ["SOL", "BTC"],
    )
    ran = mod.Today(0, frozenset(), frozenset({mod.trigger_of(found[1]).trigger_id}))
    c.eq("a trigger already run is skipped", [x.symbol for x in mod.pick(found, ran)], ["SOL", "BTC", "DOGE"])
    twice = [move(mod, "ETH", "4"), move(mod, "ETH", "3", "tg-second")]
    c.eq("one run per token, even across two events", [x.trigger_id for x in mod.pick(twice, none)], [mod.trigger_of(twice[0]).trigger_id])
    return c.bad


def check_frozen(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for label, value, member in (
        ("a move", move(mod, "BTC", "2"), "size"),
        ("a dispute", dispute(mod, "BTC", "0.1"), "gap"),
        ("today", mod.Today(0, frozenset(), frozenset()), "runs"),
        ("a pass", mod.Passed((), ()), "failed"),
    ):
        try:
            setattr(value, member, None)
            c.bad.append(f"{label} can be changed after it is made")
        except AttributeError:
            pass
    return c.bad


class Outcome:
    def __init__(self, state: str, forecast_id: str | None) -> None:
        self.state, self.forecast_id = state, forecast_id


def check_run_pass(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    league = cycle(
        "c1",
        [
            {"symbol": "BTC", "fields": f(ret1="0.025"), "seen_ms": NOON - 60_000},
            {"symbol": "ETH", "fields": f(ret1="0.04"), "seen_ms": NOON - 60_000},
            {"symbol": "SOL", "fields": f(ret1="-0.03"), "seen_ms": NOON - 60_000},
        ],
    )
    calls: list[tuple[str, str, str]] = []

    def run(trigger: Any, run_id: str) -> Outcome:
        calls.append((trigger.symbol, trigger.trigger_id, run_id))
        if trigger.symbol == "SOL":
            raise RuntimeError("the venue did not answer")
        return Outcome("COMMITTED" if trigger.symbol == "ETH" else "ABSTAINED", "fc1" if trigger.symbol == "ETH" else None)

    got = mod.run_pass(league, [run_state("r0", "DOGE", NOON - 1000, "t0")], "c1", NOON, run)
    c.eq("ran in order, within the two runs left", [x[0] for x in calls], ["ETH", "SOL"])
    c.eq("the run id is fixed by the trigger", [x[2] for x in calls], [mod.natural_id("drun", {"trigger": x[1]}) for x in calls])
    c.eq("the triggers of the pass", got.triggers, tuple(x[1] for x in calls))
    c.eq("the outcomes", got.outcomes, ((calls[0][1], "COMMITTED", "fc1"),) if calls else ())
    c.eq(
        "the failure is named and the pass goes on",
        got.failed,
        ((calls[1][1], "RuntimeError: the venue did not answer"),) if len(calls) > 1 else (),
    )
    long = mod.run_pass(league, [], "c1", NOON, lambda t, r: (_ for _ in ()).throw(ValueError("x" * 400)))
    c.eq("a long failure is cut at 200 characters", [len(x[1]) for x in long.failed], [200, 200, 200])
    c.eq("nothing to run", mod.run_pass(league, [], "c9", NOON, run), mod.Passed((), (), ()))
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "moves": check_moves,
    "today": check_today,
    "pick": check_pick,
    "disputes": check_disputes,
    "pick_disputes": check_pick_disputes,
    "run_pass": check_run_pass,
    "frozen": check_frozen,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
