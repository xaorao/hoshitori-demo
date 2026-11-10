"""What the rest of a claim depends on one part of it (contract 29, ``CHANGE_CLAIM``, in its exact form for a claim graph).

Take one part and suppose it true, suppose it false, or take it out; run the same propagation as for any check (``claim_graph``); and say
which other parts change and how. The supposed status is a stand-in written down here, not a finding: the answer is about the structure of
the claim ("these parts stand on that one"), never about whether the part is true. It is labelled hypothetical wherever it is shown.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from hoshitori.claim_graph import Graph, NodeType, propagate

TRUE: Final = "TRUE"
FALSE: Final = "FALSE"
REMOVED: Final = "REMOVED"
# the status standing for "suppose it holds" and "suppose it does not"; a part with no entry cannot be supposed either way
HOLDS: Final = {
    NodeType.OBSERVATION: "SUPPORTED",
    NodeType.INTERPRETATION: "SUPPORTED_BY_EVIDENCE",
    NodeType.CONDITION: "SUPPORTED",
    NodeType.CAUSAL_LINK: "ORDER_CONSISTENT",
    NodeType.ASSUMPTION_INFERRED: "SUPPORTED_BY_EVIDENCE",
}
FAILS: Final = {
    NodeType.OBSERVATION: "CONTRADICTED",
    NodeType.INTERPRETATION: "CONTRADICTED",
    NodeType.CONDITION: "CONTRADICTED",
    NodeType.CAUSAL_LINK: "ORDER_INCONSISTENT",
    NodeType.ASSUMPTION_INFERRED: "CONTRADICTED",
}


@dataclass(frozen=True)
class Assumption:
    node_id: str
    kind: str  # TRUE, FALSE or REMOVED
    changed: tuple[tuple[str, str, str], ...]  # (another part, its status before, its status after), in dependency order
    sentence: str


def short(text: str) -> str:
    """A part's words as the sentence quotes them: whole up to sixty characters, cut after that."""
    return text if len(text) <= 60 else text[:57].rstrip() + "..."


def assumptions(graph: Graph, checks: dict[str, str]) -> tuple[Assumption, ...]:
    """For each part that can be supposed true or false: what changes if it is, and what changes if it is taken out."""
    base = propagate(graph, checks).statuses
    out: list[Assumption] = []
    for node in graph.nodes:
        if node.type not in HOLDS:
            continue
        words = short(graph.words(node))
        for kind, status in ((TRUE, HOLDS[node.type]), (FALSE, FAILS[node.type])):
            after = propagate(graph, {**checks, node.node_id: status}).statuses
            changed = tuple((i, base[i], after[i]) for i in after if i != node.node_id and after[i] != base[i])
            out.append(Assumption(node.node_id, kind, changed, sentence_of(kind, words, len(changed))))
        kept = tuple(n for n in graph.nodes if n.node_id != node.node_id)
        edges = tuple(e for e in graph.edges if node.node_id not in (e.start, e.end))
        smaller = replace(graph, nodes=kept, edges=edges)
        gone = propagate(smaller, {k: v for k, v in checks.items() if k != node.node_id}).statuses
        changed = tuple((i, base[i], gone[i]) for i in gone if gone[i] != base[i])
        out.append(Assumption(node.node_id, REMOVED, changed, sentence_of(REMOVED, words, len(changed))))
    return tuple(out)


def sentence_of(kind: str, words: str, n: int) -> str:
    """The sentence for a supposition: its kind, the part's words (as ``short`` gives them) and how many other parts change."""
    plural = "part" if n == 1 else "parts"
    if kind == TRUE:
        what = f"If “{words}” held, {n} other {plural} would change." if n else f"If “{words}” held, no other part would change."
    elif kind == FALSE:
        what = (
            f"If “{words}” did not hold, {n} other {plural} would change."
            if n
            else f"If “{words}” did not hold, no other part would change."
        )
    else:
        what = f"Without “{words}”, {n} other {plural} would change." if n else f"Without “{words}”, no other part would change."
    return what + " A supposition, not a finding."
