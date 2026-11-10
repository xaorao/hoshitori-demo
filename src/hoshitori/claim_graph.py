"""A claim as a small graph of typed parts, each tied to the exact words it came from (spec ``CLAIM_GRAPH_SPEC``, contract 34).

A composite claim ("whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15%") is never given one
verdict. ``build_graph`` refuses anything that is not tied to the person's own words, and ``propagate`` turns the statuses that checks
gave to single parts into the statuses of the whole, by the two rules that keep it honest: support never travels along an edge, and
weakness does. Nothing here calls a model or reads a source; a status comes from a check, and no check has run until one is given.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Final

from hoshitori import typed

MAX_NODES: Final = 8
MAX_TEXT: Final = 4000
UNCHECKED: Final = "UNCHECKED"
PREMISE_CONTRADICTED: Final = "PREMISE_CONTRADICTED"
RESTS_ON_UNPROVEN_PREMISE: Final = "RESTS_ON_UNPROVEN_PREMISE"


class GraphError(ValueError):
    """A graph that cannot be built, with the reason."""


class NodeType(Enum):
    OBSERVATION = "OBSERVATION"
    INTERPRETATION = "INTERPRETATION"
    CAUSAL_LINK = "CAUSAL_LINK"
    FORECAST = "FORECAST"
    CONDITION = "CONDITION"
    OPINION = "OPINION"
    PROMOTION_SIGNAL = "PROMOTION_SIGNAL"
    CORRECTION_REF = "CORRECTION_REF"
    ASSUMPTION_INFERRED = "ASSUMPTION_INFERRED"


class EdgeKind(Enum):
    DEPENDS_ON = "DEPENDS_ON"  # (from, to): the conclusion of ``from`` needs ``to``
    CAUSAL = "CAUSAL"  # (cause, effect), as the author stated it; it carries no requirement
    CONDITIONAL_ON = "CONDITIONAL_ON"  # (from, to): ``from`` holds only if the condition ``to`` does


# The statuses a check may give to a node of each type (contract 34.3). UNCHECKED is the status of every node before a check.
STATUSES: Final[dict[NodeType, tuple[str, ...]]] = {
    NodeType.OBSERVATION: (
        "SUPPORTED",
        "CONTRADICTED",
        "MIXED",
        "NOT_FOUND_IN_SOURCES_CHECKED",
        "NOT_CHECKABLE",
        "UNRESOLVED",
        "SOURCE_UNAVAILABLE",
    ),
    NodeType.INTERPRETATION: ("SUPPORTED_BY_EVIDENCE", "CONTRADICTED", "UNPROVEN"),
    NodeType.CAUSAL_LINK: ("MECHANISM_UNTESTED", "ORDER_CONSISTENT", "ORDER_INCONSISTENT"),
    NodeType.FORECAST: ("PENDING", "SETTLED", "VOID", "UNRESOLVED"),
    NodeType.CONDITION: ("SUPPORTED", "CONTRADICTED", "UNRESOLVED"),
    NodeType.OPINION: ("OPINION_CONTEXT",),
    NodeType.PROMOTION_SIGNAL: ("DISCLOSURE_FOUND", "NOT_FOUND", "UNKNOWN"),
    NodeType.CORRECTION_REF: ("APPLIES", "DOES_NOT_APPLY"),
    NodeType.ASSUMPTION_INFERRED: ("UNPROVEN", "SUPPORTED_BY_EVIDENCE", "CONTRADICTED"),
}

# How a status reads to a person, for the sentence that names the weakest link; and how weak it is (lower is weaker).
WORDS: Final[dict[str, tuple[str, int]]] = {
    "CONTRADICTED": ("contradicted by the evidence checked", 0),
    "ORDER_INCONSISTENT": ("not in the order the claim states", 0),
    "MIXED": ("backed by some sources and contradicted by others", 1),
    "UNPROVEN": ("unproven", 2),
    "NOT_FOUND_IN_SOURCES_CHECKED": ("not found in the sources checked", 2),
    "NOT_CHECKABLE": ("not something these sources can check", 2),
    "UNRESOLVED": ("not resolved", 2),
    "SOURCE_UNAVAILABLE": ("not checked, because a source was unavailable", 2),
    "PENDING": ("a forecast that has not settled", 2),
    "VOID": ("void, so it never settled", 2),
    "OPINION_CONTEXT": ("an opinion, not evidence", 2),
    "MECHANISM_UNTESTED": ("a mechanism no source here tests", 2),
    "UNKNOWN": ("unknown", 2),
    "NOT_FOUND": ("no disclosure found", 2),
    UNCHECKED: ("not checked yet", 3),
}
ROOT_BAD: Final = frozenset(WORDS)
# What is a premise only in name: a forecast that has not settled or never will, and an opinion. What rests on one rests on
# something unproven; the sentence names one only when a conclusion needs it, since a forecast nobody builds on is simply a forecast.
UNSUPPORTED_PREMISES: Final = frozenset({"PENDING", "VOID", "OPINION_CONTEXT"})
CONTRADICTED_SET: Final = frozenset({"CONTRADICTED", "ORDER_INCONSISTENT", PREMISE_CONTRADICTED})
WEAK_SET: Final = frozenset(WORDS) - CONTRADICTED_SET | {RESTS_ON_UNPROVEN_PREMISE}  # includes UNSUPPORTED_PREMISES
TYPE_WORDS: Final = {
    NodeType.OBSERVATION: "the observation",
    NodeType.INTERPRETATION: "the interpretation",
    NodeType.CAUSAL_LINK: "the causal link",
    NodeType.FORECAST: "the forecast",
    NodeType.CONDITION: "the condition",
    NodeType.OPINION: "the opinion",
    NodeType.PROMOTION_SIGNAL: "the promotion signal",
    NodeType.CORRECTION_REF: "the correction",
    NodeType.ASSUMPTION_INFERRED: "the assumed step",
}
# What an inferred step's words could bring in (spec, rules from the source study, rule 2: an inferred node may not introduce a number,
# date or asset absent from the spans it cites). A number is digits, with a comma only between digits ("1,500" is one number, "150,
# then" reads 150) and with a minus sign ("-" or U+2212) written directly before them ("-15" is not 15; one after a letter, a digit, "_"
# or "%", as in "10-15%", is a hyphen, not a sign), or one of the number words below, in any case. Numbers are compared as they are
# written, so "15" and "fifteen", or "1,500" and "1500", are different numbers here. "one" is not read, because it is also a pronoun
# ("no one"). A date is read only through its digits; a date or a time in words ("by Friday") is not read.
NUMBER_WORDS: Final = (
    "zero",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
    "hundred",
    "thousand",
    "million",
    "billion",
    "trillion",
)
_NUMBER = re.compile(r"(?:(?<![\w%])[-\u2212])?\d+(?:,\d+)*(?:\.\d+)?|\b(?:" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)
_SYMBOL = re.compile(r"[A-Za-z0-9]+")
# The characters with the Unicode property Bidi_Control. Each can change the order in which the text around it is shown, so a claim
# that held one could show its words in another order than it holds them.
BIDI_CONTROLS: Final = "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"


@dataclass(frozen=True)
class Node:
    node_id: str
    type: NodeType
    spans: tuple[tuple[int, int], ...] = ()  # stated nodes: the exact segments of the text; inferred nodes: none
    text: str = ""  # an inferred node's own words; a stated node's words are its spans
    cites: tuple[str, ...] = ()  # an inferred node: the stated nodes that need it


@dataclass(frozen=True)
class Edge:
    start: str
    end: str
    kind: EdgeKind


@dataclass(frozen=True)
class Graph:
    text: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]

    def words(self, node: Node) -> str:
        """What the node says: the author's own words for a stated node, ours (marked inferred) for an inferred one."""
        if node.type is NodeType.ASSUMPTION_INFERRED:
            return node.text
        return " … ".join(self.text[a:b] for a, b in node.spans)

    def requires(self, node_id: str) -> tuple[str, ...]:
        """The nodes whose truth this node needs, in the order the edges were given."""
        return tuple(e.end for e in self.edges if e.start == node_id and e.kind in (EdgeKind.DEPENDS_ON, EdgeKind.CONDITIONAL_ON))


def check_text(text: str) -> None:
    """Refuses a claim that cannot be a graph's text, so that the text shows as it is written: empty, too long, not NFC, holding a
    control character (Unicode category Cc, apart from a newline and a tab), holding a bidirectional control (the Unicode property
    Bidi_Control: U+061C, U+200E, U+200F, U+202A to U+202E and U+2066 to U+2069), which can show the words around it in another order,
    holding a hidden formatting character (``typed.hidden_character``), which can carry words nobody sees,
    or not valid canonical text (a lone surrogate, a code point Unicode 15.0 does not assign), which no record, and no request digest,
    could hold. That last rule is the canonical module's, asked through ``typed.not_canonical`` and given in its words."""
    if not text.strip():
        raise GraphError("the claim has no text")
    if len(text) > MAX_TEXT:
        raise GraphError(f"the claim is longer than {MAX_TEXT} characters")
    if unicodedata.normalize("NFC", text) != text:
        raise GraphError("the claim text is not in NFC form")
    if any(unicodedata.category(c) == "Cc" and c not in ("\n", "\t") for c in text):
        raise GraphError("the claim text has a control character")
    if any(c in BIDI_CONTROLS for c in text):
        raise GraphError("the claim text has a bidirectional control character")
    reason = typed.not_canonical(text)
    if reason is not None:
        raise GraphError(f"the claim text is not valid canonical text: {reason}")
    if typed.hidden_character(text) is not None:
        raise GraphError("the claim text has a hidden formatting character")


def _brings(words: str, assets: tuple[str, ...]) -> list[str]:
    """What some words would bring into an inferred step, in the order of the words: each number as it is written (a number word in
    lower case), and each asset, a word that is one of the registry's symbols written in capitals (spec section 3 step 2)."""
    found = [(m.start(), m.group().lower()) for m in _NUMBER.finditer(words)]
    found += [(m.start(), m.group()) for m in _SYMBOL.finditer(words) if m.group() in assets and m.group().isupper()]
    return [x for _, x in sorted(found)]


def _check_node(text: str, node: Node, known: set[str]) -> None:
    if not node.node_id or node.node_id in known:
        raise GraphError(f"{node.node_id!r}: a node needs an id used once")
    inferred = node.type is NodeType.ASSUMPTION_INFERRED
    if inferred:
        if node.spans or not node.text.strip() or not node.cites:
            raise GraphError(f"{node.node_id}: an inferred node has words of its own, no spans and at least one node that needs it")
        return
    if node.text or node.cites or not node.spans:
        raise GraphError(f"{node.node_id}: a stated node has spans and nothing else")
    last = 0
    for a, b in node.spans:
        if not 0 <= a < b <= len(text) or a < last:
            raise GraphError(f"{node.node_id}: a span is not inside the text, in order and apart from the others")
        last = b
    if not any(text[a:b].strip() for a, b in node.spans):
        raise GraphError(f"{node.node_id}: a stated node has at least one visible character")


def _acyclic(ids: list[str], edges: tuple[Edge, ...]) -> None:
    needs = {i: {e.end for e in edges if e.start == i and e.kind is not EdgeKind.CAUSAL} for i in ids}
    left = set(ids)
    while left:
        free = {i for i in left if not needs[i] & left}
        if not free:
            raise GraphError("the dependencies form a loop")
        left -= free


def build_graph(text: str, nodes: tuple[Node, ...], edges: tuple[Edge, ...] = (), assets: tuple[str, ...] = ()) -> Graph:
    """A graph, or the reason it cannot be: every stated part tied to the exact words, nothing inferred that brings in a number or an
    asset the words it cites lack. ``assets`` are the symbols the registry knows, in capitals; with none, no word is read as an asset."""
    check_text(text)
    if not nodes:
        raise GraphError("a graph has at least one node")
    if len(nodes) > MAX_NODES:
        raise GraphError(f"a graph has at most {MAX_NODES} nodes; choose the claim to audit")
    known: set[str] = set()
    for node in nodes:
        _check_node(text, node, known)
        known.add(node.node_id)
    by_id = {n.node_id: n for n in nodes}
    made = list(edges)
    for node in (n for n in nodes if n.type is NodeType.ASSUMPTION_INFERRED):
        for cited in node.cites:
            if cited not in by_id or by_id[cited].type is NodeType.ASSUMPTION_INFERRED:
                raise GraphError(f"{node.node_id}: it cites {cited!r}, which is not a stated node")
        graph_so_far = Graph(text, nodes, ())
        allowed = {m for c in node.cites for m in _brings(graph_so_far.words(by_id[c]), assets)}
        invented = [m for m in _brings(node.text, assets) if m not in allowed]
        if invented:
            raise GraphError(f"{node.node_id}: it brings in {invented[0]}, which the words it cites do not contain")
        made += [Edge(c, node.node_id, EdgeKind.DEPENDS_ON) for c in node.cites if Edge(c, node.node_id, EdgeKind.DEPENDS_ON) not in made]
    for e in made:
        if e.start not in by_id or e.end not in by_id or e.start == e.end:
            raise GraphError("an edge joins two different nodes of the graph")
    if len(set(made)) != len(made):
        raise GraphError("an edge is given twice")
    _acyclic([n.node_id for n in nodes], tuple(made))
    return Graph(text, nodes, tuple(made))


def order(graph: Graph) -> tuple[str, ...]:
    """Node ids with every node after the nodes it requires; nodes with no order between them keep the order they were given in."""
    placed: list[str] = []
    waiting = [n.node_id for n in graph.nodes]
    while waiting:
        ready = next(i for i in waiting if all(r in placed for r in graph.requires(i)))
        placed.append(ready)
        waiting.remove(ready)
    return tuple(placed)


@dataclass(frozen=True)
class Result:
    statuses: dict[str, str]  # the status of every node after propagation
    own: dict[str, str]  # what each node's own check said (UNCHECKED where none ran)
    sentence: str


def propagate(graph: Graph, checks: dict[str, str]) -> Result:
    """The status of every node from the checks given to single nodes, and one sentence naming the weakest link."""
    by_id = {n.node_id: n for n in graph.nodes}
    for node_id, status in checks.items():
        if node_id not in by_id:
            raise GraphError(f"a check was given for {node_id!r}, which is not a node")
        if status not in STATUSES[by_id[node_id].type]:
            raise GraphError(f"{node_id}: {status} is not a status of {by_id[node_id].type.value}")
    own = {n.node_id: checks.get(n.node_id, UNCHECKED) for n in graph.nodes}
    final: dict[str, str] = {}
    for node_id in order(graph):
        needed = [final[r] for r in graph.requires(node_id)]
        if any(s in CONTRADICTED_SET for s in needed):
            final[node_id] = PREMISE_CONTRADICTED
        elif own[node_id] in CONTRADICTED_SET:
            final[node_id] = own[node_id]  # a part that was itself checked and does not hold stays so, whatever its premises
        elif any(s in WEAK_SET for s in needed):
            final[node_id] = RESTS_ON_UNPROVEN_PREMISE
        else:
            final[node_id] = own[node_id]
    return Result(final, own, _sentence(graph, own))


def _quote(text: str) -> str:
    return text if len(text) <= 60 else text[:57].rstrip() + "..."


def _sentence(graph: Graph, own: dict[str, str]) -> str:
    """Names the weakest required link by the node's own status: the first one, in dependency order, among the weakest.

    A link is a node that another node requires, or a causal link whose stated order fails; a conclusion nothing builds on, and a
    causal link nobody has tested (which is every one, by default), are not what the argument stands on, so they are not named. A part
    that was checked and does not hold is always named, even when nothing builds on it: saying that every part holds would be false."""
    needed = {r for n in graph.nodes for r in graph.requires(n.node_id)}
    required = needed or {n.node_id for n in graph.nodes}
    bad = [
        (WORDS[own[i]][1], k, i)
        for k, i in enumerate(order(graph))
        if own[i] in ROOT_BAD and (i in needed if own[i] in UNSUPPORTED_PREMISES else i in required or own[i] in CONTRADICTED_SET)
    ]
    if not bad:
        return "Every part that was checked holds on the evidence checked; no causal claim is marked as supported."
    _, _, weakest = min(bad)
    node = next(n for n in graph.nodes if n.node_id == weakest)
    return f"The weakest link is {TYPE_WORDS[node.type]} “{_quote(graph.words(node))}”: {WORDS[own[weakest]][0]}."
