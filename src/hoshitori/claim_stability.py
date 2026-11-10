"""How far independent readings of one claim agree (spec ``CLAIM_GRAPH_SPEC`` section 4, contract 34.2 and 34.11).

Agreement across runs finds a reader that wavers; it does not show a stable reading to be right, because runs of one model share its
blind spots. So the state is read together with what the plain second reader found, and it says whether the decomposition rules were
ever checked against labelled claims. No run is a source of truth and nothing here changes a graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hoshitori.claim_graph import Graph, Node, NodeType
from hoshitori.claim_segmenter import Disagreement

OVERLAP: Final = 0.5  # two nodes of two runs are the same node when their words overlap by at least this much (intersection over union)
THRESHOLD: Final = 0.7  # a prior; it is to be set from the labelled set, not from the readers' agreement with each other


class StabilityError(ValueError):
    """Readings that cannot be compared."""


@dataclass(frozen=True)
class Decomposition:
    state: str  # STABLE, UNSTABLE or DISPUTED
    score: float  # the share of nodes that every run found
    validated: bool  # whether the rules were checked against labelled claims
    unmatched: tuple[str, ...]  # nodes of the first run that some run did not find
    type_conflicts: tuple[str, ...]  # nodes of the first run found elsewhere as another kind of node
    disagreements: tuple[Disagreement, ...]

    def shown(self) -> tuple[str, ...]:
        """The states to show: the reading's own, and that it was never validated when it was not."""
        return (self.state,) if self.validated else (self.state, "NOT_VALIDATED")


def _chars(node: Node) -> set[int]:
    return {i for a, b in node.spans for i in range(a, b)}


def overlap(first: Node, second: Node) -> float:
    """Intersection over union of the characters two nodes stand on."""
    a, b = _chars(first), _chars(second)
    return len(a & b) / len(a | b)


def _stated(graph: Graph) -> list[Node]:
    return [n for n in graph.nodes if n.type is not NodeType.ASSUMPTION_INFERRED]


def decompose(runs: tuple[Graph, ...], found: tuple[Disagreement, ...], validated: bool = False) -> Decomposition:
    """The state of a claim's decomposition from independent readings and the disagreements of the plain reader."""
    if len(runs) < 2:
        raise StabilityError("at least two readings are needed to compare")
    if len({g.text for g in runs}) != 1:
        raise StabilityError("the readings are of different texts")
    first = _stated(runs[0])
    unmatched: set[str] = set()
    conflicts: set[str] = set()
    for other in runs[1:]:
        free = _stated(other)
        for node in first:
            best = max(free, key=lambda m: overlap(node, m), default=None)
            if best is None or overlap(node, best) < OVERLAP:
                unmatched.add(node.node_id)
                continue
            free.remove(best)
            if best.type is not node.type:
                conflicts.add(node.node_id)
    widest = max(len(_stated(g)) for g in runs)
    score = (len(first) - len(unmatched)) / widest
    state = "DISPUTED" if found else ("STABLE" if score >= THRESHOLD else "UNSTABLE")
    ordered = [n.node_id for n in first]
    return Decomposition(
        state,
        score,
        validated,
        tuple(i for i in ordered if i in unmatched),
        tuple(i for i in ordered if i in conflicts),
        found,
    )
