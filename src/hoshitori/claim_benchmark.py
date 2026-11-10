"""Scoring a claim reader against labelled claims, and how far two people who labelled them agree (contract 34.11, part 3).

The labelled set is people's work: composite claims, each cut into a graph by two annotators who then settled their differences. This module
is only the measuring stick, so that the day the set exists the numbers come from code that was tested on planted cases, and a reader (the
model arm, or the plain segmenter) is judged the same way every time.

* **A node is matched by its words.** A stated node of a candidate graph matches a labelled node when they cover exactly the same segments
  of the text; each labelled node is matched at most once. An inferred step (a premise the words need but do not state) matches when it
  cites the same stated nodes, so two readers that pick different wording for the same implied step are not penalised for the wording.
* **Six rates**, each as a count over a count with a Wilson 95% interval and never a bare ratio: span match (labelled stated nodes the
  candidate found), type accuracy (of those, how many got the same type), missing premise (labelled inferred steps the candidate did not
  find), spurious node (candidate nodes that match nothing labelled), edge accuracy (labelled edges the candidate has, with the same kind
  between the matched nodes) and spurious edge (candidate edges that are not labelled).
* **A reader that gave no graph** (it failed, and said so) scores as a failure and counts every labelled part as missed: a failure is never
  dropped from the denominator.
* **Strata** (nested conditionals, negations, quoted third parties, a causal word that is only a conjunction and the like) are reported
  apart, because an average hides the case that breaks it.
* **Agreement of two annotators** is the share of nodes whose words they cut alike, Cohen's kappa on the type of those nodes, and the share
  of edges they drew alike. A low agreement is a finding about the guide, not about the reader.

Nothing here decides a threshold. They are fixed after a pilot (contract 34.11), written down before the scored run, and kept in the plan.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from hoshitori.belief_revision import wilson
from hoshitori.claim_graph import Edge, EdgeKind, Graph, GraphError, Node, NodeType, build_graph

FORMAT: Final = 1
STRATA: Final = (
    "plain",
    "nested_conditional",
    "negation",
    "quoted_third_party",
    "conjunction_not_cause",
    "no_testable_content",
    "long",
)
MIN_ANNOTATORS: Final = 2


class BenchmarkError(ValueError):
    """A labelled set that cannot be used."""


@dataclass(frozen=True)
class LabelledCase:
    case_id: str
    stratum: str
    gold: Graph


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    stratum: str
    failed: bool  # the reader gave no graph
    gold_stated: int
    span_matched: int
    type_right: int
    gold_inferred: int
    inferred_found: int
    candidate_nodes: int
    spurious_nodes: int
    gold_edges: int
    edges_found: int
    candidate_edges: int
    spurious_edges: int


@dataclass(frozen=True)
class Rate:
    hits: int
    total: int
    rate: float | None  # none when there is nothing to divide by: never a zero that looks like a result
    low: float | None
    high: float | None


@dataclass(frozen=True)
class Report:
    cases: int
    failed: int
    span_match: Rate
    type_accuracy: Rate
    missing_premise: Rate
    spurious_node: Rate
    edge_accuracy: Rate
    spurious_edge: Rate


@dataclass(frozen=True)
class Agreement:
    nodes_alike: Rate  # of the nodes either annotator cut, those both cut with the same words
    type_kappa: float | None  # Cohen's kappa on the type of the nodes both cut alike; none when it is undefined
    edges_alike: Rate


def _spans(node: Node) -> tuple[tuple[int, int], ...]:
    return node.spans  # build_graph already requires the segments in order and apart


def _stated(graph: Graph) -> list[Node]:
    return [n for n in graph.nodes if n.type is not NodeType.ASSUMPTION_INFERRED]


def _inferred(graph: Graph) -> list[Node]:
    return [n for n in graph.nodes if n.type is NodeType.ASSUMPTION_INFERRED]


def _match_stated(gold: Graph, candidate: Graph) -> dict[str, str]:
    """Candidate node id to labelled node id, by identical segments, each labelled node used once."""
    free = {_spans(n): n.node_id for n in _stated(gold)}
    out: dict[str, str] = {}
    for n in _stated(candidate):
        key = _spans(n)
        if key in free:
            out[n.node_id] = free.pop(key)
    return out


def _match_inferred(gold: Graph, candidate: Graph, stated: dict[str, str]) -> dict[str, str]:
    free = {frozenset(n.cites): n.node_id for n in _inferred(gold)}
    out: dict[str, str] = {}
    for n in _inferred(candidate):
        mapped = frozenset(stated.get(c, "") for c in n.cites)
        if mapped in free:
            out[n.node_id] = free.pop(mapped)
    return out


def score_case(case: LabelledCase, candidate: Graph | None) -> CaseScore:
    """One labelled claim against one reading of it (``None`` for a reader that failed)."""
    gold = case.gold
    gold_stated, gold_inferred, gold_edges = len(_stated(gold)), len(_inferred(gold)), len(gold.edges)
    if candidate is None:
        return CaseScore(case.case_id, case.stratum, True, gold_stated, 0, 0, gold_inferred, 0, 0, 0, gold_edges, 0, 0, 0)
    if candidate.text != gold.text:
        raise BenchmarkError(f"{case.case_id}: the reading is of other words than the labelled claim")
    stated = _match_stated(gold, candidate)
    inferred = _match_inferred(gold, candidate, stated)
    matched = {**stated, **inferred}
    gold_type = {n.node_id: n.type for n in gold.nodes}
    cand_type = {n.node_id: n.type for n in candidate.nodes}
    type_right = sum(1 for c, g in stated.items() if cand_type[c] is gold_type[g])
    gold_edge_set = {(e.start, e.end, e.kind) for e in gold.edges}
    mapped_edges = {(matched[e.start], matched[e.end], e.kind) for e in candidate.edges if e.start in matched and e.end in matched}
    found = len(mapped_edges & gold_edge_set)
    spurious_edges = len(candidate.edges) - found
    return CaseScore(
        case.case_id,
        case.stratum,
        False,
        gold_stated,
        len(stated),
        type_right,
        gold_inferred,
        len(inferred),
        len(candidate.nodes),
        len(candidate.nodes) - len(matched),
        gold_edges,
        found,
        len(candidate.edges),
        spurious_edges,
    )


def _rate(hits: int, total: int) -> Rate:
    if total == 0:
        return Rate(hits, 0, None, None, None)
    low, high = wilson(hits, total)
    return Rate(hits, total, hits / total, low, high)


def report(scores: Sequence[CaseScore]) -> Report:
    """The six rates over all the cases given; ask once for every stratum to see them apart."""
    s = list(scores)

    def total(name: str) -> int:
        return sum(getattr(x, name) for x in s)

    gold_inferred, found = total("gold_inferred"), total("inferred_found")
    return Report(
        len(s),
        sum(1 for x in s if x.failed),
        _rate(total("span_matched"), total("gold_stated")),
        _rate(total("type_right"), total("span_matched")),
        _rate(gold_inferred - found, gold_inferred),
        _rate(total("spurious_nodes"), total("candidate_nodes")),
        _rate(total("edges_found"), total("gold_edges")),
        _rate(total("spurious_edges"), total("candidate_edges")),
    )


def by_stratum(scores: Sequence[CaseScore]) -> dict[str, Report]:
    return {name: report([x for x in scores if x.stratum == name]) for name in STRATA if any(x.stratum == name for x in scores)}


def agreement(a: Graph, b: Graph) -> Agreement:
    """How alike two annotators cut one claim."""
    if a.text != b.text:
        raise BenchmarkError("the two annotations are of other words")
    stated = _match_stated(a, b)  # b's node to a's
    cut_either = len(_stated(a)) + len(_stated(b)) - len(stated)
    types_a = {n.node_id: n.type for n in a.nodes}
    types_b = {n.node_id: n.type for n in b.nodes}
    pairs = [(types_a[g], types_b[c]) for c, g in stated.items()]
    matched = {**stated, **_match_inferred(a, b, stated)}
    edges_a = {(e.start, e.end, e.kind) for e in a.edges}
    edges_b = {(matched[e.start], matched[e.end], e.kind) for e in b.edges if e.start in matched and e.end in matched}
    either_edges = len(edges_a) + len(b.edges) - len(edges_a & edges_b)
    return Agreement(_rate(len(stated), cut_either), kappa(pairs), _rate(len(edges_a & edges_b), either_edges))


def kappa(pairs: Sequence[tuple[NodeType, NodeType]]) -> float | None:
    """Cohen's kappa of two raters' labels; none when chance agreement is complete (the ratio is then 0/0) or there is nothing."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for x, y in pairs if x is y) / n
    left, right = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    chance = sum(left[k] * right[k] for k in left) / (n * n)
    if chance == 1:
        return None
    return (observed - chance) / (1 - chance)


def pooled_agreement(items: Sequence[tuple[Graph, Graph]]) -> Agreement:
    """The agreement over many claims, counts added before any ratio is made."""
    node_hits = node_total = edge_hits = edge_total = 0
    pairs: list[tuple[NodeType, NodeType]] = []
    for a, b in items:
        one = agreement(a, b)
        node_hits, node_total = node_hits + one.nodes_alike.hits, node_total + one.nodes_alike.total
        edge_hits, edge_total = edge_hits + one.edges_alike.hits, edge_total + one.edges_alike.total
        matched = _match_stated(a, b)
        types_a = {n.node_id: n.type for n in a.nodes}
        types_b = {n.node_id: n.type for n in b.nodes}
        pairs += [(types_a[g], types_b[c]) for c, g in matched.items()]
    return Agreement(_rate(node_hits, node_total), kappa(pairs), _rate(edge_hits, edge_total))


# --- the labelled set as a file --------------------------------------------------------------------------------------------------------
def _graph(text: str, body: Any, where: str) -> Graph:
    if not isinstance(body, dict) or set(body) != {"nodes", "edges"}:
        raise BenchmarkError(f"{where}: a graph has nodes and edges")
    nodes: list[Node] = []
    for raw in body["nodes"]:
        if not isinstance(raw, dict) or set(raw) != {"id", "type", "spans", "text", "cites"}:
            raise BenchmarkError(f"{where}: a node has id, type, spans, text and cites")
        try:
            kind = NodeType(raw["type"])
        except ValueError:
            raise BenchmarkError(f"{where}: {raw['type']!r} is not a node type") from None
        spans = tuple(tuple(s) for s in raw["spans"])
        if not all(len(s) == 2 and all(isinstance(i, int) and not isinstance(i, bool) for i in s) for s in spans):
            raise BenchmarkError(f"{where}: a span is two whole numbers")
        nodes.append(Node(str(raw["id"]), kind, spans, str(raw["text"]), tuple(str(c) for c in raw["cites"])))
    edges: list[Edge] = []
    for raw in body["edges"]:
        if not isinstance(raw, dict) or set(raw) != {"start", "end", "kind"}:
            raise BenchmarkError(f"{where}: an edge has start, end and kind")
        try:
            edges.append(Edge(str(raw["start"]), str(raw["end"]), EdgeKind(raw["kind"])))
        except ValueError:
            raise BenchmarkError(f"{where}: {raw['kind']!r} is not an edge kind") from None
    try:
        return build_graph(text, tuple(nodes), tuple(edges), ())
    except GraphError as exc:
        raise BenchmarkError(f"{where}: the labelled graph is not a graph the product accepts: {exc}") from exc


def read_set(data: bytes) -> tuple[LabelledCase, ...]:
    """A labelled set, strictly: every labelled graph must be one the product itself would accept, so a label cannot be easier than the
    reader's own rules."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"the labelled set is not JSON: {exc}") from exc
    if not isinstance(doc, dict) or set(doc) != {"format", "annotators", "cases"} or doc["format"] != FORMAT:
        raise BenchmarkError(f"a labelled set has format {FORMAT}, annotators and cases")
    names = doc["annotators"]
    if not isinstance(names, list) or len(set(map(str, names))) < MIN_ANNOTATORS:
        raise BenchmarkError(f"a labelled set names at least {MIN_ANNOTATORS} different annotators")
    cases: list[LabelledCase] = []
    for raw in doc["cases"]:
        if not isinstance(raw, dict) or set(raw) != {"id", "stratum", "text", "gold"}:
            raise BenchmarkError("a case has id, stratum, text and gold")
        case_id = str(raw["id"])
        if any(c.case_id == case_id for c in cases):
            raise BenchmarkError(f"{case_id}: a case is named once")
        if raw["stratum"] not in STRATA:
            raise BenchmarkError(f"{case_id}: the stratum is one of {list(STRATA)}")
        if not isinstance(raw["text"], str):
            raise BenchmarkError(f"{case_id}: the text is text")
        cases.append(LabelledCase(case_id, raw["stratum"], _graph(raw["text"], raw["gold"], case_id)))
    if not cases:
        raise BenchmarkError("a labelled set has at least one case")
    return tuple(cases)
