"""The Desk's hypotheses, written from a closed list of templates (contract section 57; Desk study Q30).

A hypothesis is a fixed sentence filled with the symbol, the number of hours and the direction of a move that a trigger reported, and
nothing else, so no text from a source can reach a hypothesis and an injected instruction cannot create one. It is a small claim graph: the
observation that the price moved, and the condition that the move began after the announcement, which the falsification step tests. The
pre-test target is a prior written down here before any test is run (a thesis the Desk would hold if nothing disproved it); a move down is
a flat hypothesis, because the Desk is long-only. A disagreement between agents (ADR-031) has its own template, ``SIDE_UP``: the agents
disagree on the token over the next window, and the up reading holds; it has no event time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from hoshitori import canonical, desk_gates
from hoshitori.claim_graph import Edge, EdgeKind, Graph, Node, NodeType, build_graph
from hoshitori.desk_record import ROUTES
from hoshitori.ids import natural_id

TEMPLATES: Final = ("MOVE_UP", "MOVE_DOWN", "SIDE_UP")
KINDS: Final = ("MOVE", "DISAGREEMENT")
DIRECTIONS: Final = ("UP", "DOWN")
PRE_P_LONG: Final = Decimal("0.60")  # a prior, written before any test: what the Desk would hold if nothing disproved the thesis
PRE_WEIGHT_LONG: Final = Decimal("0.03")
TARGET_NODE: Final = "cond"  # the assumption the falsification step tests: that the move began after the announcement
TRIAGE_RULE_VERSION: Final = 1
MAX_HOURS: Final = 24
_SYMBOL = re.compile(r"[A-Z0-9]{1,20}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_CONDITION: Final = "The move began after the announcement."
_INVALIDATION: Final = "The move began before the announcement or the market moved alike."
_WORDS: Final = {"MOVE_UP": "rose", "MOVE_DOWN": "fell"}
_SIDE_CONDITION: Final = "The up reading holds."
_SIDE_INVALIDATION: Final = "The independent evidence does not support the up reading."


class PlanError(ValueError):
    """A trigger that cannot be turned into a hypothesis."""


@dataclass(frozen=True)
class Trigger:
    trigger_id: str | None
    route: str
    symbol: str
    direction: str
    hours: int
    event_time_ms: int | None  # when the announcement was; a trigger that came from the registry has none
    kind: str = "MOVE"  # MOVE: a move after an event; DISAGREEMENT: agents on opposite sides of a call, which has no event time


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    template_id: str
    statement: str
    invalidation: str
    graph: Graph
    stance: str
    pre_target: desk_gates.PreTarget
    discovery_route: str
    direction: str  # UP or DOWN, the way the move the hypothesis speaks of went
    hours: int  # the window it speaks of


def _check(trigger: Trigger) -> None:
    if not isinstance(trigger.symbol, str) or _SYMBOL.fullmatch(trigger.symbol) is None:
        raise PlanError("symbol is 1 to 20 capital letters or digits")
    if trigger.direction not in DIRECTIONS:
        raise PlanError(f"direction is one of {DIRECTIONS}")
    if trigger.route not in ROUTES:
        raise PlanError(f"route is one of {ROUTES}")
    if isinstance(trigger.hours, bool) or not isinstance(trigger.hours, int) or not 1 <= trigger.hours <= MAX_HOURS:
        raise PlanError(f"hours is a whole number from 1 to {MAX_HOURS}")
    if trigger.kind not in KINDS:
        raise PlanError(f"kind is one of {KINDS}")
    if trigger.kind == "DISAGREEMENT" and (trigger.direction != "UP" or trigger.event_time_ms is not None):
        raise PlanError("a disagreement tests the up reading and has no event time")
    when = trigger.event_time_ms
    if when is not None and (isinstance(when, bool) or not isinstance(when, int) or when < 0):
        raise PlanError("event_time_ms is a whole number of milliseconds, or none")
    named = trigger.trigger_id
    if named is not None and (not isinstance(named, str) or _ID.fullmatch(named) is None):
        raise PlanError("trigger_id is an id, or none")


def hypotheses(trigger: Trigger) -> tuple[Hypothesis, ...]:
    """The hypotheses to investigate for a trigger, in the order to investigate them (one, today)."""
    _check(trigger)
    window = "hour" if trigger.hours == 1 else f"{trigger.hours} hours"
    if trigger.kind == "DISAGREEMENT":
        template, condition, invalidation = "SIDE_UP", _SIDE_CONDITION, _SIDE_INVALIDATION
        observation = f"The agents disagree on {trigger.symbol} over the next {window}."
    else:
        template = "MOVE_UP" if trigger.direction == "UP" else "MOVE_DOWN"
        condition, invalidation = _CONDITION, _INVALIDATION
        observation = f"{trigger.symbol} {_WORDS[template]} over the last {window}."
    statement = f"{observation} {condition}"
    nodes = (
        Node("obs", NodeType.OBSERVATION, spans=((0, len(observation)),)),
        Node(TARGET_NODE, NodeType.CONDITION, spans=((len(observation) + 1, len(statement)),)),
    )
    graph = build_graph(statement, nodes, (Edge(TARGET_NODE, "obs", EdgeKind.DEPENDS_ON),), (trigger.symbol,))
    stance = "FLAT" if template == "MOVE_DOWN" else "LONG"
    if stance == "LONG":
        pre = desk_gates.PreTarget("LONG", PRE_P_LONG, PRE_WEIGHT_LONG)
    else:
        pre = desk_gates.PreTarget("FLAT", Decimal("0.5"), Decimal(0))
    key: dict[str, canonical.JsonValue] = {
        "template": template,
        "symbol": trigger.symbol,
        "hours": trigger.hours,
        "trigger": trigger.trigger_id,
    }
    return (
        Hypothesis(
            natural_id("hy", key),
            template,
            statement,
            invalidation,
            graph,
            stance,
            pre,
            trigger.route,
            trigger.direction,
            trigger.hours,
        ),
    )


def record_body(run_id: str, hypothesis: Hypothesis) -> dict[str, canonical.JsonValue]:
    """The body of the ``hypothesis`` record of a run."""
    if not isinstance(run_id, str) or _ID.fullmatch(run_id) is None:
        raise PlanError("run_id is an id")
    graph = hypothesis.graph
    return {
        "run_id": run_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "template_id": hypothesis.template_id,
        "statement": hypothesis.statement,
        "invalidation": hypothesis.invalidation,
        "graph": {
            "nodes": [{"node_id": n.node_id, "type": n.type.value, "span": [n.spans[0][0], n.spans[0][1]]} for n in graph.nodes],
            "edges": [{"from": e.start, "to": e.end, "kind": e.kind.value} for e in graph.edges],
        },
        "stance": hypothesis.stance,
        "discovery_route": hypothesis.discovery_route,
        "triage_rule_version": TRIAGE_RULE_VERSION,
    }
