"""The next check of a claim (contract 44), by the dependency-order rule.

One function, ``select_next_check``, that anything showing a next step calls; there is no second rule. It is the plain baseline of the
policies of contract 43, not the value-based one: that needs estimates earned from recorded menus the product has not collected. The
rule is that a downstream part is never checked before an upstream part that is still open, and that only a check that exists is
offered. A check is registered only if the person can carry it out at a named primary source today; a check the product could run
itself is added when it is wired to a source, not before. A part nothing registered can resolve ends in NO_AVAILABLE_CHECK with the
reason, and never in an invented step.

The same function serves the Research Desk. The Desk passes a registry built from its menu of gateway checks (``registry_from_menu``) and
the ids of the checks it has already tried; the Claim reader passes nothing and keeps the person registry, so its next checks are unchanged.
With a menu the answer also lists the alternatives in the order they would be offered and says which rule chose. ``outcome`` maps a result
onto the closed lists of stops and abstentions, so a run never records a reason of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hoshitori import desk_menu
from hoshitori.claim_graph import UNCHECKED, Graph, NodeType, order

PERSON: Final = "PERSON"  # the person carries it out at the named source
CHECK: Final = "CHECK"
NO_AVAILABLE_CHECK: Final = "NO_AVAILABLE_CHECK"
NOTHING_LEFT: Final = "NOTHING_LEFT"
GATEWAY: Final = "GATEWAY"  # the product carries it out through the gateway
STOP: Final = "STOP"
ABSTAIN: Final = "ABSTAIN"
# statuses of a part that has been looked at without an answer: another check is needed, and the same one would not help
TRIED: Final = frozenset({"NOT_CHECKABLE", "NOT_FOUND_IN_SOURCES_CHECKED", "SOURCE_UNAVAILABLE"})
OPEN: Final = TRIED | {UNCHECKED, "UNPROVEN", "UNRESOLVED", "MIXED", "MECHANISM_UNTESTED", "UNKNOWN", "NOT_FOUND"}
DECIDED_AGAINST: Final = frozenset({"CONTRADICTED", "ORDER_INCONSISTENT"})


@dataclass(frozen=True)
class Check:
    check_id: str
    node_type: NodeType
    mode: str
    instruction: str
    source: str


REGISTRY: Final = (
    Check(
        "compare_exchange_candles",
        NodeType.OBSERVATION,
        PERSON,
        "Compare the figure with the exchange's own candles for the same period.",
        "the exchange's public price chart",
    ),
    Check(
        "find_primary_announcement",
        NodeType.CONDITION,
        PERSON,
        "Find the announcement itself, not a report of it.",
        "the issuer's or regulator's own announcement page",
    ),
    Check(
        "look_for_disclosure",
        NodeType.PROMOTION_SIGNAL,
        PERSON,
        "Look for a disclosure of payment or holdings by the author.",
        "the author's own profile and the post",
    ),
    Check(
        "find_corrected_claim",
        NodeType.CORRECTION_REF,
        PERSON,
        "Find the claim this corrects and see whether it is the one being read.",
        "the author's own earlier post",
    ),
)
NO_CHECK_REASONS: Final = {
    NodeType.INTERPRETATION: "what lies behind a movement (who, why) cannot be checked with the sources available here",
    NodeType.CAUSAL_LINK: "a market cause cannot be checked here, only whether the order in time fits",
    NodeType.FORECAST: "a forecast is checked by waiting for its outcome",
    NodeType.OPINION: "an opinion has no check",
    NodeType.ASSUMPTION_INFERRED: "an implied step has no source; confirm or reject it",
}
TRIED_REASON: Final = "the check that exists was tried and gave no answer"
AGAINST_REASON: Final = "a part the claim needs has been checked and does not hold"
DONE_REASON: Final = "every part the claim needs has been checked"
ALL_TRIED_REASON: Final = "every check on the menu for this part was tried"
NO_MENU_REASON: Final = "no check on the menu can resolve this part"
MENU_EXPLANATION: Final = (
    "dependency order: the first open part is offered; uncalibrated, so the preferred check type and then the cheaper one is offered"
)
# the order in which the kinds of check are preferred for a part, before cost: the independent price check first, as the Claim reader does
PREFERENCE: Final = ("INDEPENDENT_PRICE_CHECK", "DERIVATIVES_CONTEXT", "RYO_TOKEN_READ")


@dataclass(frozen=True)
class NextCheck:
    status: str  # CHECK, NO_AVAILABLE_CHECK or NOTHING_LEFT
    node_id: str | None
    words: str | None  # what the part says
    check: Check | None
    reason: str | None
    required: bool = True  # whether the claim needs this part
    alternatives: tuple[str, ...] = ()  # the other checks that could be offered for it, in order (menu only)
    explanation: str = ""  # which rule chose (menu only)


class NotOnMenu(ValueError):
    """A check id that is not on the menu was requested."""


def registry_from_menu(menu: desk_menu.Menu) -> tuple[Check, ...]:
    """The checks of a menu that can resolve a part of a claim, in the order they are offered: by preferred kind, then by expected seconds
    (a check with no recorded latency after those with one), then by id. Only observations are resolved by the checks the product serves
    today; what RYO's deep analysis says is an interpretation of its own and checks no part of a claim."""
    chosen = [c for c in menu.checks if c.node_type == "OBSERVATION" and c.check_type in PREFERENCE]

    def rank(c: desk_menu.MenuCheck) -> tuple[int, int, str, str]:
        seconds = c.cost.seconds
        return (
            PREFERENCE.index(c.check_type),
            0 if seconds is not None else 1,
            f"{float(seconds):012.3f}" if seconds is not None else "",
            c.check_id,
        )

    chosen.sort(key=rank)
    symbol = dict(chosen[0].args).get("symbol", "") if chosen else ""
    return tuple(
        Check(c.check_id, NodeType.OBSERVATION, GATEWAY, f"Call {c.provider} {c.operation} for {symbol}.", c.provider) for c in chosen
    )


def resolve_request(registry: tuple[Check, ...], check_id: str) -> Check:
    """The check a request names, or a refusal: nothing outside the menu can be run, whoever asks."""
    for check in registry:
        if check.check_id == check_id:
            return check
    raise NotOnMenu(f"not on the menu: {check_id!r}")


def outcome(result: NextCheck) -> tuple[str, str] | None:
    """The closed stop or abstention word for a result that ends the run, or ``None`` while there is a check to run."""
    if result.status == CHECK:
        return None
    if result.status == NOTHING_LEFT:
        return (STOP, "ALL_MENU_OPENED") if result.reason == DONE_REASON else (ABSTAIN, "EVIDENCE_CONFLICT_UNRESOLVED")
    return (ABSTAIN, "ESSENTIAL_PREMISE_UNPROVEN") if result.required else (STOP, "ALL_MENU_OPENED")


def select_next_check(
    graph: Graph,
    own: dict[str, str],
    registry: tuple[Check, ...] = REGISTRY,
    tried: frozenset[str] | None = None,
) -> NextCheck:
    """The next check, from the status each part's own check gave it (UNCHECKED where none has run).

    ``registry`` is the checks that may be offered. ``tried`` is ``None`` for the person's registry: a part that has been looked at without
    an answer is not sent back to the same check. With a menu it is the set of check ids already tried: a part with such a status is offered
    the next check of the menu that has not been tried, and ends in NO_AVAILABLE_CHECK only when none is left."""
    by_id = {n.node_id: n for n in graph.nodes}
    sequence = order(graph)
    required = {r for n in graph.nodes for r in graph.requires(n.node_id)} or set(sequence)
    for node_id in sequence:
        if own.get(node_id, UNCHECKED) in DECIDED_AGAINST and node_id in required:
            return NextCheck(NOTHING_LEFT, node_id, graph.words(by_id[node_id]), None, AGAINST_REASON)
    menu_mode = tried is not None
    spent = tried or frozenset()
    for node_id in sequence:
        node = by_id[node_id]
        status = own.get(node_id, UNCHECKED)
        of_type = [c for c in registry if c.node_type is node.type]
        candidates = [c for c in of_type if c.check_id not in spent]
        registered = candidates[0] if candidates else None
        if status not in OPEN or (node_id not in required and not of_type):
            continue  # a part nothing builds on and nothing can check is not a step the person is sent to
        words = graph.words(node)
        is_required = node_id in required
        if not of_type:
            reason = NO_CHECK_REASONS.get(node.type, NO_MENU_REASON)
            return NextCheck(NO_AVAILABLE_CHECK, node_id, words, None, reason, is_required)
        if registered is None:
            return NextCheck(NO_AVAILABLE_CHECK, node_id, words, None, ALL_TRIED_REASON if menu_mode else TRIED_REASON, is_required)
        if status in TRIED and not menu_mode:
            return NextCheck(NO_AVAILABLE_CHECK, node_id, words, None, TRIED_REASON, is_required)
        if menu_mode:
            return NextCheck(
                CHECK, node_id, words, registered, None, is_required, tuple(c.check_id for c in candidates[1:]), MENU_EXPLANATION
            )
        return NextCheck(CHECK, node_id, words, registered, None, is_required)
    return NextCheck(NOTHING_LEFT, None, None, None, DONE_REASON)
