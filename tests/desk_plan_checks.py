"""Checks of the Desk's hypotheses (``hoshitori.desk_plan``), runnable against a mutant.

A hypothesis is written from a closed template and nothing else: the symbol, the number of hours and the direction fill fixed sentences, so
no text from any source can reach a hypothesis and an injected instruction cannot create one.
"""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Any

from hoshitori import claim_graph, desk_record


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.PlanError as exc:
            if says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to contain {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def trigger(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "trigger_id": "trg-1",
        "route": "EVENT",
        "symbol": "BTC",
        "direction": "UP",
        "hours": 1,
        "event_time_ms": 1793275000000,
    }
    values.update(over)
    return mod.Trigger(**values)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("templates", mod.TEMPLATES, ("MOVE_UP", "MOVE_DOWN", "SIDE_UP"))
    c.eq("kinds of trigger", mod.KINDS, ("MOVE", "DISAGREEMENT"))
    c.eq("directions", mod.DIRECTIONS, ("UP", "DOWN"))
    c.eq("priors of a long thesis", (mod.PRE_P_LONG, mod.PRE_WEIGHT_LONG), (Decimal("0.60"), Decimal("0.03")))
    c.eq("the target node of a hypothesis", mod.TARGET_NODE, "cond")
    c.eq("frozen", [k.__dataclass_params__.frozen for k in (mod.Trigger, mod.Hypothesis)], [True, True])
    return c.bad


def check_up(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    found = mod.hypotheses(trigger(mod))
    c.eq("one hypothesis for an upward move", len(found), 1)
    h = found[0]
    c.eq("the template and stance", (h.template_id, h.stance, h.discovery_route), ("MOVE_UP", "LONG", "EVENT"))
    c.eq("the direction and the hours it speaks of", (h.direction, h.hours), ("UP", 1))
    c.eq("four hours", mod.hypotheses(trigger(mod, hours=4))[0].hours, 4)
    c.eq("the statement is the template filled", h.statement, "BTC rose over the last hour. The move began after the announcement.")
    c.eq("and the invalidation", h.invalidation, "The move began before the announcement or the market moved alike.")
    g = h.graph
    c.eq(
        "two nodes in order",
        [(n.node_id, n.type) for n in g.nodes],
        [("obs", claim_graph.NodeType.OBSERVATION), ("cond", claim_graph.NodeType.CONDITION)],
    )
    c.eq("their words", [g.words(n) for n in g.nodes], ["BTC rose over the last hour.", "The move began after the announcement."])
    c.eq(
        "the condition needs the observation",
        [(e.start, e.end, e.kind) for e in g.edges],
        [("cond", "obs", claim_graph.EdgeKind.DEPENDS_ON)],
    )
    c.eq("the observation is what the claim requires", g.requires("cond"), ("obs",))
    c.eq("the pre-test target", (h.pre_target.stance, h.pre_target.p, h.pre_target.weight), ("LONG", Decimal("0.60"), Decimal("0.03")))
    c.eq("a repeat gives the same", mod.hypotheses(trigger(mod)), found)
    c.eq(
        "one hour is singular and more are plural",
        [mod.hypotheses(trigger(mod, hours=n))[0].statement.split(".")[0] for n in (1, 2, 24)],
        ["BTC rose over the last hour", "BTC rose over the last 2 hours", "BTC rose over the last 24 hours"],
    )
    c.eq("the id of the default hypothesis (a pinned regression value)", h.hypothesis_id, "hy7lndfbhcnfluklnhbmoc6hoar5")
    ids = {
        mod.hypotheses(trigger(mod, **over))[0].hypothesis_id
        for over in ({}, {"hours": 4}, {"symbol": "ETH"}, {"trigger_id": "trg-2"}, {"trigger_id": None})
    }
    c.eq("another trigger, size of window or symbol is another hypothesis", len(ids), 5)
    c.eq(
        "the event time does not change who the hypothesis is",
        mod.hypotheses(trigger(mod, event_time_ms=5))[0].hypothesis_id,
        h.hypothesis_id,
    )
    c.eq("a window of 24 hours", mod.hypotheses(trigger(mod, hours=24))[0].statement.startswith("BTC rose over the last 24 hours."), True)
    return c.bad


def check_side(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    t = trigger(mod, kind="DISAGREEMENT", event_time_ms=None, route="WATCHLIST", hours=4)
    found = mod.hypotheses(t)
    c.eq("one hypothesis for a disagreement", len(found), 1)
    h = found[0]
    c.eq("the template, stance and route", (h.template_id, h.stance, h.discovery_route), ("SIDE_UP", "LONG", "WATCHLIST"))
    c.eq("the direction and the hours", (h.direction, h.hours), ("UP", 4))
    c.eq("the statement is the template filled", h.statement, "The agents disagree on BTC over the next 4 hours. The up reading holds.")
    c.eq("and the invalidation", h.invalidation, "The independent evidence does not support the up reading.")
    c.eq(
        "one hour is singular",
        mod.hypotheses(trigger(mod, kind="DISAGREEMENT", event_time_ms=None, hours=1))[0].statement.split(".")[0],
        "The agents disagree on BTC over the next hour",
    )
    g = h.graph
    c.eq(
        "two nodes in order",
        [(n.node_id, n.type) for n in g.nodes],
        [("obs", claim_graph.NodeType.OBSERVATION), ("cond", claim_graph.NodeType.CONDITION)],
    )
    c.eq("their words", [g.words(n) for n in g.nodes], ["The agents disagree on BTC over the next 4 hours.", "The up reading holds."])
    c.eq("the condition needs the observation", g.requires("cond"), ("obs",))
    c.eq(
        "the pre-test target is the long prior",
        (h.pre_target.stance, h.pre_target.p, h.pre_target.weight),
        ("LONG", Decimal("0.60"), Decimal("0.03")),
    )
    move_id = mod.hypotheses(trigger(mod, hours=4, event_time_ms=None, route="WATCHLIST"))[0].hypothesis_id
    c.eq("a disagreement is not the move of the same token and window", h.hypothesis_id != move_id, True)
    for label, over, says in (
        ("a disagreement that tests the down reading", {"kind": "DISAGREEMENT", "direction": "DOWN", "event_time_ms": None}, "up reading"),
        ("a disagreement with an event time", {"kind": "DISAGREEMENT"}, "event time"),
        ("a kind that is not one", {"kind": "RUMOUR"}, "kind"),
    ):
        c.refuses(label, lambda over=over: mod.hypotheses(trigger(mod, **over)), says)
    c.eq("a move is the default kind", trigger(mod).kind, "MOVE")
    return c.bad


def check_down(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    h = mod.hypotheses(trigger(mod, direction="DOWN", route="RYO_SCAN"))[0]
    c.eq("a downward move is a flat hypothesis", (h.template_id, h.stance, h.discovery_route), ("MOVE_DOWN", "FLAT", "RYO_SCAN"))
    c.eq("the direction", (h.direction, h.hours), ("DOWN", 1))
    c.eq("with its own words", h.statement, "BTC fell over the last hour. The move began after the announcement.")
    c.eq("and a target of nothing", (h.pre_target.stance, h.pre_target.p, h.pre_target.weight), ("FLAT", Decimal("0.5"), Decimal("0")))
    return c.bad


def check_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    h = mod.hypotheses(trigger(mod))[0]
    body = mod.record_body("run-1", h)
    try:
        desk_record.check_body("hypothesis", body)
    except desk_record.DeskRecordError as exc:
        c.bad.append(f"the record body is refused: {exc}")
    c.eq(
        "the record body",
        {k: body[k] for k in ("run_id", "hypothesis_id", "template_id", "statement", "stance", "discovery_route", "triage_rule_version")},
        {
            "run_id": "run-1",
            "hypothesis_id": h.hypothesis_id,
            "template_id": "MOVE_UP",
            "statement": h.statement,
            "stance": "LONG",
            "discovery_route": "EVENT",
            "triage_rule_version": 1,
        },
    )
    c.eq(
        "the graph with spans",
        body["graph"],
        {
            "nodes": [
                {"node_id": "obs", "type": "OBSERVATION", "span": [0, 28]},
                {"node_id": "cond", "type": "CONDITION", "span": [29, 67]},
            ],
            "edges": [{"from": "cond", "to": "obs", "kind": "DEPENDS_ON"}],
        },
    )
    down = mod.record_body("run-2", mod.hypotheses(trigger(mod, direction="DOWN"))[0])
    try:
        desk_record.check_body("hypothesis", down)
    except desk_record.DeskRecordError as exc:
        c.bad.append(f"the downward record body is refused: {exc}")
    c.refuses("a run id that is not an id", lambda: mod.record_body("bad id!", h), "run_id")
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for label, over, says in (
        ("a symbol in lower case", {"symbol": "btc"}, "symbol"),
        ("a symbol with a space", {"symbol": "B TC"}, "symbol"),
        ("an empty symbol", {"symbol": ""}, "symbol"),
        ("a symbol of 21 characters", {"symbol": "A" * 21}, "symbol"),
        ("a symbol that is an instruction", {"symbol": "IGNORE-THE-RULES"}, "symbol"),
        ("a direction that does not exist", {"direction": "SIDEWAYS"}, "direction"),
        ("a route that is not one", {"route": "USER_CLAIM"}, "route"),
        ("zero hours", {"hours": 0}, "hours"),
        ("25 hours", {"hours": 25}, "hours"),
        ("fractional hours", {"hours": 1.5}, "hours"),
        ("boolean hours", {"hours": True}, "hours"),
        ("a negative event time", {"event_time_ms": -1}, "event_time_ms"),
        ("a boolean event time", {"event_time_ms": True}, "event_time_ms"),
        ("a trigger id that is not an id", {"trigger_id": "bad id!"}, "trigger_id"),
    ):
        c.refuses(label, lambda over=over: mod.hypotheses(trigger(mod, **over)), says)
    for label, over in (
        ("no event time", {"event_time_ms": None}),
        ("event time zero", {"event_time_ms": 0}),
        ("no trigger id", {"trigger_id": None}),
        ("24 hours", {"hours": 24}),
        ("one hour", {"hours": 1}),
        ("a symbol of 20", {"symbol": "A" * 20}),
        ("digits in a symbol", {"symbol": "1INCH"}),
    ):
        try:
            mod.hypotheses(trigger(mod, **over))
        except Exception as exc:
            c.bad.append(f"{label} was refused: {exc}")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_up, check_side, check_down, check_record, check_refusals):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
