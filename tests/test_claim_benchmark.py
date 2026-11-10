"""The measuring stick for a claim reader (``hoshitori.claim_benchmark``), tested on planted cases whose right scores are worked out by hand."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hoshitori import claim_benchmark as cb
from hoshitori.claim_graph import Edge, EdgeKind, Graph, Node, NodeType, build_graph

TEXT = "Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week."


def span(words: str) -> tuple[int, int]:
    start = TEXT.index(words)
    return (start, start + len(words))


OBS, INTERP, FORECAST = "Whales are accumulating SOL", "institutions are entering", "SOL will rise 15% this week"


def graph(
    types: tuple[NodeType, NodeType, NodeType] = (NodeType.OBSERVATION, NodeType.INTERPRETATION, NodeType.FORECAST),
    *,
    inferred: bool = True,
    edges: tuple[Edge, ...] | None = None,
    extra: bool = False,
    text: str = TEXT,
) -> Graph:
    nodes = [
        Node("n1", types[0], (span(OBS),)),
        Node("n2", types[1], (span(INTERP),)),
        Node("n3", types[2], (span(FORECAST),)),
    ]
    if inferred:
        nodes.append(Node("a1", NodeType.ASSUMPTION_INFERRED, (), "the accumulating wallets belong to institutions", ("n1",)))
    if extra:
        nodes.append(Node("n4", NodeType.OPINION, (span("this week"),)))
    made = edges if edges is not None else (Edge("n1", "n2", EdgeKind.CAUSAL), Edge("n3", "n2", EdgeKind.DEPENDS_ON))
    return build_graph(text, tuple(nodes), made, ())


def case(stratum: str = "plain", name: str = "c1") -> cb.LabelledCase:
    return cb.LabelledCase(name, stratum, graph())


def test_a_perfect_reading_scores_everything_found_and_nothing_spurious() -> None:
    score = cb.score_case(case(), graph())
    rep = cb.report([score])
    assert (rep.cases, rep.failed) == (1, 0)
    assert (rep.span_match.hits, rep.span_match.total, rep.span_match.rate) == (3, 3, 1.0)
    assert (rep.type_accuracy.hits, rep.type_accuracy.total) == (3, 3)
    assert (rep.missing_premise.hits, rep.missing_premise.total) == (0, 1)
    assert (rep.spurious_node.hits, rep.spurious_node.total) == (0, 4)
    assert (rep.edge_accuracy.hits, rep.edge_accuracy.total) == (3, 3)  # the two stated edges and the edge the inferred step adds
    assert (rep.spurious_edge.hits, rep.spurious_edge.total) == (0, 3)
    assert (
        rep.span_match.low is not None and 0.43 < rep.span_match.low < 0.44 and rep.span_match.high == pytest.approx(1.0)
    )  # Wilson, 3 of 3


def test_a_wrong_type_lowers_type_accuracy_and_nothing_else() -> None:
    wrong = graph((NodeType.OBSERVATION, NodeType.OBSERVATION, NodeType.FORECAST))
    rep = cb.report([cb.score_case(case(), wrong)])
    assert (rep.span_match.hits, rep.type_accuracy.hits, rep.type_accuracy.total) == (3, 2, 3)
    assert (rep.spurious_node.hits, rep.missing_premise.hits) == (0, 0)


def test_a_missing_premise_and_a_spurious_node_and_a_spurious_edge_are_counted() -> None:
    no_premise = graph(inferred=False)
    rep = cb.report([cb.score_case(case(), no_premise)])
    assert (rep.missing_premise.hits, rep.missing_premise.total) == (1, 1)
    assert (rep.edge_accuracy.hits, rep.edge_accuracy.total) == (2, 3)  # the edge the inferred step brought is gone with it
    extra = graph(
        extra=True, edges=(Edge("n1", "n2", EdgeKind.CAUSAL), Edge("n3", "n2", EdgeKind.DEPENDS_ON), Edge("n4", "n3", EdgeKind.DEPENDS_ON))
    )
    rep = cb.report([cb.score_case(case(), extra)])
    assert (rep.spurious_node.hits, rep.spurious_node.total) == (1, 5)
    assert (rep.spurious_edge.hits, rep.spurious_edge.total) == (1, 4)
    wrong_kind = graph(edges=(Edge("n1", "n2", EdgeKind.DEPENDS_ON), Edge("n3", "n2", EdgeKind.DEPENDS_ON)))
    rep = cb.report([cb.score_case(case(), wrong_kind)])
    assert (rep.edge_accuracy.hits, rep.spurious_edge.hits) == (2, 1)  # the same pair with another kind is not the labelled edge


def test_an_implied_step_is_matched_by_what_it_cites_and_not_by_its_wording() -> None:
    reworded = build_graph(
        TEXT,
        (
            Node("n1", NodeType.OBSERVATION, (span(OBS),)),
            Node("n2", NodeType.INTERPRETATION, (span(INTERP),)),
            Node("n3", NodeType.FORECAST, (span(FORECAST),)),
            Node("x", NodeType.ASSUMPTION_INFERRED, (), "those wallets are held by funds", ("n1",)),
        ),
        (Edge("n1", "n2", EdgeKind.CAUSAL), Edge("n3", "n2", EdgeKind.DEPENDS_ON)),
        (),
    )
    rep = cb.report([cb.score_case(case(), reworded)])
    assert (rep.missing_premise.hits, rep.spurious_node.hits) == (0, 0)


def test_a_reader_that_failed_is_a_failure_and_misses_everything_but_is_not_dropped() -> None:
    rep = cb.report([cb.score_case(case(), None), cb.score_case(case(name="c2"), graph())])
    assert (rep.cases, rep.failed) == (2, 1)
    assert (rep.span_match.hits, rep.span_match.total) == (3, 6)
    assert (rep.missing_premise.hits, rep.missing_premise.total) == (1, 2)
    assert (rep.edge_accuracy.hits, rep.edge_accuracy.total) == (3, 6)


def test_a_reading_of_other_words_is_refused_and_an_empty_report_says_nothing_instead_of_zero() -> None:
    other = build_graph("SOL is going up.", (Node("n1", NodeType.FORECAST, ((0, 15),)),), (), ())
    with pytest.raises(cb.BenchmarkError, match="c1: the reading is of other words than the labelled claim"):
        cb.score_case(case(), other)
    rep = cb.report([])
    assert rep.cases == 0 and rep.span_match == cb.Rate(0, 0, None, None, None) and rep.spurious_node.rate is None
    nothing_cut = cb.report(
        [
            cb.score_case(
                cb.LabelledCase("c", "plain", build_graph(TEXT, (Node("n1", NodeType.OPINION, (span(OBS),)),), (), ())),
                build_graph(TEXT, (Node("n1", NodeType.OPINION, (span(OBS),)),), (), ()),
            )
        ]
    )
    assert (
        nothing_cut.missing_premise.rate is None and nothing_cut.edge_accuracy.total == 0
    )  # no inferred step and no edge to find: no rate


def test_strata_are_reported_apart_in_the_order_of_the_list() -> None:
    scores = [
        cb.score_case(case("negation", "a"), graph()),
        cb.score_case(case("plain", "b"), None),
        cb.score_case(case("plain", "c"), graph()),
    ]
    parts = cb.by_stratum(scores)
    assert list(parts) == ["plain", "negation"]
    assert (parts["plain"].cases, parts["plain"].failed, parts["plain"].span_match.total) == (2, 1, 6)
    assert (parts["negation"].cases, parts["negation"].span_match.hits) == (1, 3)


def test_two_annotators_who_cut_alike_agree_and_one_who_cuts_otherwise_does_not() -> None:
    same = cb.agreement(graph(), graph())
    assert (same.nodes_alike.hits, same.nodes_alike.total) == (3, 3) and same.type_kappa == 1.0
    assert (same.edges_alike.hits, same.edges_alike.total) == (3, 3)
    other = graph((NodeType.OBSERVATION, NodeType.OBSERVATION, NodeType.FORECAST), extra=True)
    some = cb.agreement(graph(), other)
    assert (some.nodes_alike.hits, some.nodes_alike.total) == (3, 4)  # the fourth node is cut by one annotator only
    assert some.type_kappa == pytest.approx(
        0.5
    )  # worked by hand: 2 of 3 alike (2/3); chance (1*2 + 1*0 + 1*1)/9 = 1/3; (2/3 - 1/3)/(1 - 1/3)
    with pytest.raises(cb.BenchmarkError, match="the two annotations are of other words"):
        cb.agreement(graph(), build_graph("SOL is going up.", (Node("n1", NodeType.FORECAST, ((0, 15),)),), (), ()))


def test_kappa_is_none_where_it_is_undefined_and_exact_where_it_is_not() -> None:
    assert cb.kappa([]) is None
    assert cb.kappa([(NodeType.OBSERVATION, NodeType.OBSERVATION)] * 3) is None  # both always say one thing: chance is complete
    pairs = [(NodeType.OBSERVATION, NodeType.OBSERVATION)] * 2 + [(NodeType.FORECAST, NodeType.OBSERVATION)] * 2
    assert cb.kappa(pairs) == pytest.approx(0.0)
    assert cb.kappa([(NodeType.OBSERVATION, NodeType.FORECAST), (NodeType.FORECAST, NodeType.OBSERVATION)]) == pytest.approx(-1.0)


def test_pooled_agreement_adds_counts_before_it_divides() -> None:
    pooled = cb.pooled_agreement([(graph(), graph()), (graph(), graph(extra=True))])
    assert (pooled.nodes_alike.hits, pooled.nodes_alike.total) == (6, 7)
    assert pooled.type_kappa == 1.0


def labelled(**over: Any) -> bytes:
    gold = {
        "nodes": [
            {"id": "n1", "type": "OBSERVATION", "spans": [list(span(OBS))], "text": "", "cites": []},
            {"id": "n2", "type": "INTERPRETATION", "spans": [list(span(INTERP))], "text": "", "cites": []},
            {"id": "n3", "type": "FORECAST", "spans": [list(span(FORECAST))], "text": "", "cites": []},
        ],
        "edges": [{"start": "n1", "end": "n2", "kind": "CAUSAL"}],
    }
    doc: dict[str, Any] = {"format": 1, "annotators": ["a", "b"], "cases": [{"id": "c1", "stratum": "plain", "text": TEXT, "gold": gold}]}
    doc.update(over)
    return json.dumps(doc).encode()


def test_a_labelled_set_is_read_strictly_and_its_graphs_must_be_ones_the_product_accepts() -> None:
    (one,) = cb.read_set(labelled())
    assert (one.case_id, one.stratum, len(one.gold.nodes), len(one.gold.edges)) == ("c1", "plain", 3, 1)

    def refuses(data: bytes, says: str) -> None:
        with pytest.raises(cb.BenchmarkError) as raised:
            cb.read_set(data)
        assert str(raised.value) == says

    refuses(b"{", "the labelled set is not JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)")
    refuses(b"[]", "a labelled set has format 1, annotators and cases")
    refuses(labelled(format=2), "a labelled set has format 1, annotators and cases")
    refuses(labelled(annotators=["a"]), "a labelled set names at least 2 different annotators")
    refuses(labelled(annotators=["a", "a"]), "a labelled set names at least 2 different annotators")
    refuses(labelled(cases=[]), "a labelled set has at least one case")
    base = json.loads(labelled())["cases"][0]
    refuses(labelled(cases=[base, base]), "c1: a case is named once")
    refuses(labelled(cases=[{**base, "stratum": "odd"}]), f"c1: the stratum is one of {list(cb.STRATA)}")
    refuses(labelled(cases=[{**base, "text": 3}]), "c1: the text is text")
    refuses(labelled(cases=[{"id": "c1"}]), "a case has id, stratum, text and gold")
    refuses(labelled(cases=[{**base, "gold": []}]), "c1: a graph has nodes and edges")
    bad_node = {**base["gold"], "nodes": [{"id": "n1"}]}
    refuses(labelled(cases=[{**base, "gold": bad_node}]), "c1: a node has id, type, spans, text and cites")
    typed = {**base["gold"], "nodes": [{**base["gold"]["nodes"][0], "type": "HUNCH"}]}
    refuses(labelled(cases=[{**base, "gold": typed}]), "c1: 'HUNCH' is not a node type")
    spanned = {**base["gold"], "nodes": [{**base["gold"]["nodes"][0], "spans": [[1]]}]}
    refuses(labelled(cases=[{**base, "gold": spanned}]), "c1: a span is two whole numbers")
    bad_edge = {**base["gold"], "edges": [{"start": "n1"}]}
    refuses(labelled(cases=[{**base, "gold": bad_edge}]), "c1: an edge has start, end and kind")
    kind = {**base["gold"], "edges": [{"start": "n1", "end": "n2", "kind": "MAYBE"}]}
    refuses(labelled(cases=[{**base, "gold": kind}]), "c1: 'MAYBE' is not an edge kind")
    paraphrase = {**base["gold"], "nodes": [{**base["gold"]["nodes"][0], "spans": [[0, 5]]}, *base["gold"]["nodes"][1:]]}
    with pytest.raises(cb.BenchmarkError, match="c1: the labelled graph is not a graph the product accepts"):
        cb.read_set(labelled(cases=[{**base, "gold": {**paraphrase, "edges": [{"start": "n1", "end": "n1", "kind": "CAUSAL"}]}}]))


def test_the_strata_are_these_and_a_failed_reading_scores_exactly_this() -> None:
    assert cb.STRATA == (
        "plain",
        "nested_conditional",
        "negation",
        "quoted_third_party",
        "conjunction_not_cause",
        "no_testable_content",
        "long",
    )
    failed = cb.score_case(case(), None)
    assert failed == cb.CaseScore("c1", "plain", True, 3, 0, 0, 1, 0, 0, 0, 3, 0, 0, 0)


def test_an_annotator_edge_to_a_node_the_other_did_not_cut_is_a_difference_and_not_a_crash() -> None:
    other = graph(
        extra=True, edges=(Edge("n1", "n2", EdgeKind.CAUSAL), Edge("n3", "n2", EdgeKind.DEPENDS_ON), Edge("n4", "n3", EdgeKind.DEPENDS_ON))
    )
    some = cb.agreement(graph(), other)
    assert (some.edges_alike.hits, some.edges_alike.total) == (3, 4)
    pooled = cb.pooled_agreement([(graph(), graph()), (graph(), graph(extra=True))])
    assert (pooled.edges_alike.hits, pooled.edges_alike.total) == (6, 6)


def test_a_malformed_graph_or_span_in_a_labelled_set_is_refused_in_words() -> None:
    base = json.loads(labelled())["cases"][0]

    def refuses(gold: Any, says: str) -> None:
        with pytest.raises(cb.BenchmarkError) as raised:
            cb.read_set(labelled(cases=[{**base, "gold": gold}]))
        assert str(raised.value) == says

    node = base["gold"]["nodes"][0]
    refuses({"nodes": []}, "c1: a graph has nodes and edges")
    refuses({**base["gold"], "nodes": [{**node, "spans": [[True, 3]]}]}, "c1: a span is two whole numbers")
    refuses({**base["gold"], "nodes": [{**node, "spans": [list(span(OBS)), [1]]}]}, "c1: a span is two whole numbers")
    refuses({**base["gold"], "nodes": [{**node, "spans": [list(span(OBS)), [1, "x"]]}]}, "c1: a span is two whole numbers")
