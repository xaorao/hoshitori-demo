"""Checks of the claim graph (``hoshitori.claim_graph``), runnable against a mutant: the golden composite claim, every refusal, and the
rules of propagation as properties."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

TEXT = "Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week."


def span(part: str) -> tuple[int, int]:
    i = TEXT.index(part)
    return (i, i + len(part))


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
        except self.mod.GraphError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except TimeoutError:
            raise  # the sweep's limit on a mutant that never ends must not be taken for a refusal
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")

    def accepts(self, label: str, call: Any) -> None:
        try:
            call()
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: refused with {type(exc).__name__}: {exc}")


def inferred(mod: types.ModuleType, text: str, cited: str, words: str, assets: tuple[str, ...] = ()) -> Any:
    """A graph of one stated part, the words ``cited`` of ``text``, and one inferred step in the words ``words`` that the part needs."""
    i = text.index(cited)
    nodes = (
        mod.Node("f", mod.NodeType.FORECAST, spans=((i, i + len(cited)),)),
        mod.Node("i", mod.NodeType.ASSUMPTION_INFERRED, text=words, cites=("f",)),
    )
    return mod.build_graph(text, nodes, assets=assets) if assets else mod.build_graph(text, nodes)


def lacks(what: str) -> str:
    return f"i: it brings in {what}, which the words it cites do not contain"


def golden(mod: types.ModuleType) -> Any:
    n, e = mod.Node, mod.Edge
    t = mod.NodeType
    nodes = (
        n("obs", t.OBSERVATION, spans=(span("Whales are accumulating SOL"),)),
        n("who", t.INTERPRETATION, spans=(span("institutions are entering"),)),
        n("why", t.CAUSAL_LINK, spans=(span("therefore"),)),
        n("up", t.FORECAST, spans=(span("SOL will rise 15% this week"),)),
        n("ident", t.ASSUMPTION_INFERRED, text="The large wallets belong to institutions", cites=("who",)),
    )
    edges = (
        e("who", "obs", mod.EdgeKind.DEPENDS_ON),
        e("obs", "who", mod.EdgeKind.CAUSAL),
        e("up", "who", mod.EdgeKind.DEPENDS_ON),
    )
    return mod.build_graph(TEXT, nodes, edges)


def check_golden_graph(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(mod)
    c.eq("five nodes", [x.node_id for x in g.nodes], ["obs", "who", "why", "up", "ident"])
    c.eq("the inferred node adds its dependency itself", mod.Edge("who", "ident", mod.EdgeKind.DEPENDS_ON) in g.edges, True)
    # the direction of a causal edge: from the cause to the effect, as the author states it, between the parts that are the
    # cause and the effect and never at the words that say so ("therefore" is a link node, not a cause or an effect)
    causal = [(x.start, x.end) for x in g.edges if x.kind is mod.EdgeKind.CAUSAL]
    c.eq("the golden claim's one causal edge runs from the cause to the effect", causal, [("obs", "who")])
    c.eq(
        "and none joins the words that say it",
        [x for x in g.edges if x.kind is mod.EdgeKind.CAUSAL and "why" in (x.start, x.end)],
        [],
    )
    c.eq(
        "the convention is stated where the kind is defined",
        "(cause, effect)" in (mod.__doc__ or "") + Path(mod.__file__).read_text(),
        True,
    )
    c.eq("a stated node's words are the author's", g.words(g.nodes[0]), "Whales are accumulating SOL")
    c.eq("an inferred node's words are ours", g.words(g.nodes[4]), "The large wallets belong to institutions")
    c.eq("what the forecast requires", g.requires("up"), ("who",))
    c.eq("what the interpretation requires", g.requires("who"), ("obs", "ident"))
    c.eq("a causal edge requires nothing", g.requires("why"), ())
    c.eq("order puts a node after what it requires", mod.order(g), ("obs", "why", "ident", "who", "up"))
    c.eq("the limits", (mod.MAX_NODES, mod.MAX_TEXT), (8, 4000))
    two = mod.Node("a", mod.NodeType.OBSERVATION, spans=(span("Whales"), span("accumulating")))
    c.eq("a node may have two spans, joined for reading", mod.build_graph(TEXT, (two,)).words(two), "Whales … accumulating")
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    n, e, t = mod.Node, mod.Edge, mod.NodeType
    ok = n("a", t.OBSERVATION, spans=(span("Whales"),))
    c.refuses("empty text", lambda: mod.build_graph("  ", (ok,)), "the claim has no text")
    c.refuses("long text", lambda: mod.build_graph("x" * 4001, (ok,)), "the claim is longer than 4000 characters")
    c.eq("a text of exactly the limit is accepted", len(mod.build_graph("x" * 4000, (n("a", t.OBSERVATION, spans=((0, 4),)),)).nodes), 1)
    c.refuses("not NFC", lambda: mod.build_graph("é", (n("a", t.OBSERVATION, spans=((0, 2),)),)), "the claim text is not in NFC form")
    c.refuses(
        "control", lambda: mod.build_graph("a\x00b", (n("a", t.OBSERVATION, spans=((0, 1),)),)), "the claim text has a control character"
    )
    c.eq(
        "a newline and a tab are not control characters here",
        len(mod.build_graph("a\n\tb", (n("a", t.OBSERVATION, spans=((0, 1),)),)).nodes),
        1,
    )
    c.refuses("no nodes", lambda: mod.build_graph(TEXT, ()), "a graph has at least one node")
    visible = [i for i, ch in enumerate(TEXT) if not ch.isspace()][:9]
    nine = tuple(n(f"n{k}", t.OBSERVATION, spans=((i, i + 1),)) for k, i in enumerate(visible))
    c.refuses("nine nodes", lambda: mod.build_graph(TEXT, nine), "a graph has at most 8 nodes; choose the claim to audit")
    c.eq("eight nodes", len(mod.build_graph(TEXT, nine[:8]).nodes), 8)
    c.refuses("empty id", lambda: mod.build_graph(TEXT, (n("", t.OBSERVATION, spans=((0, 1),)),)), "'': a node needs an id used once")
    c.refuses("repeated id", lambda: mod.build_graph(TEXT, (ok, ok)), "'a': a node needs an id used once")
    c.refuses(
        "span past the end",
        lambda: mod.build_graph("abc", (n("a", t.OBSERVATION, spans=((0, 4),)),)),
        "a: a span is not inside the text, in order and apart from the others",
    )
    c.refuses(
        "empty span",
        lambda: mod.build_graph("abc", (n("a", t.OBSERVATION, spans=((1, 1),)),)),
        "a: a span is not inside the text, in order and apart from the others",
    )
    c.refuses(
        "negative span",
        lambda: mod.build_graph("abc", (n("a", t.OBSERVATION, spans=((-1, 2),)),)),
        "a: a span is not inside the text, in order and apart from the others",
    )
    c.refuses(
        "spans out of order",
        lambda: mod.build_graph("abcdef", (n("a", t.OBSERVATION, spans=((3, 5), (0, 2))),)),
        "a: a span is not inside the text, in order and apart from the others",
    )
    c.refuses(
        "spans touching overlap",
        lambda: mod.build_graph("abcdef", (n("a", t.OBSERVATION, spans=((0, 3), (2, 5))),)),
        "a: a span is not inside the text, in order and apart from the others",
    )
    c.eq("spans that touch end to end", len(mod.build_graph("abcdef", (n("a", t.OBSERVATION, spans=((0, 3), (3, 5))),)).nodes), 1)
    c.refuses(
        "stated without spans", lambda: mod.build_graph(TEXT, (n("a", t.OBSERVATION),)), "a: a stated node has spans and nothing else"
    )
    c.refuses(
        "stated with text",
        lambda: mod.build_graph(TEXT, (n("a", t.OBSERVATION, spans=((0, 1),), text="x"),)),
        "a: a stated node has spans and nothing else",
    )
    c.refuses(
        "stated with cites",
        lambda: mod.build_graph(TEXT, (n("a", t.OBSERVATION, spans=((0, 1),), cites=("b",)),)),
        "a: a stated node has spans and nothing else",
    )
    inferred = "a: an inferred node has words of its own, no spans and at least one node that needs it"
    c.refuses(
        "inferred with a span",
        lambda: mod.build_graph(TEXT, (ok, n("i", t.ASSUMPTION_INFERRED, spans=((0, 1),), text="x", cites=("a",)))),
        inferred.replace("a:", "i:"),
    )
    c.refuses(
        "inferred without words",
        lambda: mod.build_graph(TEXT, (ok, n("i", t.ASSUMPTION_INFERRED, text=" ", cites=("a",)))),
        inferred.replace("a:", "i:"),
    )
    c.refuses(
        "inferred without a citation",
        lambda: mod.build_graph(TEXT, (ok, n("i", t.ASSUMPTION_INFERRED, text="x"))),
        inferred.replace("a:", "i:"),
    )
    c.refuses(
        "inferred cites nothing real",
        lambda: mod.build_graph(TEXT, (ok, n("i", t.ASSUMPTION_INFERRED, text="x", cites=("zz",)))),
        "i: it cites 'zz', which is not a stated node",
    )
    two = (ok, n("i", t.ASSUMPTION_INFERRED, text="x", cites=("a",)), n("j", t.ASSUMPTION_INFERRED, text="y", cites=("i",)))
    c.refuses("inferred cites inferred", lambda: mod.build_graph(TEXT, two), "j: it cites 'i', which is not a stated node")
    # the words are the ones the step cites, the stated nodes that need it (spec: "absent from the spans it cites"); the step does not
    # rest on them, they rest on it
    c.refuses(
        "inferred brings a number",
        lambda: mod.build_graph(TEXT, (ok, n("i", t.ASSUMPTION_INFERRED, text="It will rise 20%", cites=("a",)))),
        "i: it brings in 20, which the words it cites do not contain",
    )
    c.refuses(
        "inferred brings a comma number",
        lambda: mod.build_graph(TEXT, (ok, n("i", t.ASSUMPTION_INFERRED, text="Above 1,500 coins", cites=("a",)))),
        "i: it brings in 1,500, which the words it cites do not contain",
    )
    c.refuses(
        "inferred brings a decimal",
        lambda: mod.build_graph(TEXT, (ok, n("i", t.ASSUMPTION_INFERRED, text="About 2.5 times", cites=("a",)))),
        "i: it brings in 2.5, which the words it cites do not contain",
    )
    fifteen = n("f", t.FORECAST, spans=(span("SOL will rise 15% this week"),))
    c.eq(
        "inferred may repeat a number the words contain",
        len(mod.build_graph(TEXT, (fifteen, n("i", t.ASSUMPTION_INFERRED, text="A 15% move is large", cites=("f",)))).nodes),
        2,
    )
    c.refuses(
        "edge to nowhere",
        lambda: mod.build_graph(TEXT, (ok,), (e("a", "zz", mod.EdgeKind.DEPENDS_ON),)),
        "an edge joins two different nodes of the graph",
    )
    c.refuses(
        "edge from nowhere",
        lambda: mod.build_graph(TEXT, (ok,), (e("zz", "a", mod.EdgeKind.DEPENDS_ON),)),
        "an edge joins two different nodes of the graph",
    )
    c.refuses(
        "edge to itself",
        lambda: mod.build_graph(TEXT, (ok,), (e("a", "a", mod.EdgeKind.DEPENDS_ON),)),
        "an edge joins two different nodes of the graph",
    )
    b = n("b", t.OBSERVATION, spans=(span("institutions"),))
    c.refuses("edge twice", lambda: mod.build_graph(TEXT, (ok, b), (e("a", "b", mod.EdgeKind.DEPENDS_ON),) * 2), "an edge is given twice")
    c.refuses(
        "loop",
        lambda: mod.build_graph(TEXT, (ok, b), (e("a", "b", mod.EdgeKind.DEPENDS_ON), e("b", "a", mod.EdgeKind.CONDITIONAL_ON))),
        "the dependencies form a loop",
    )
    c.eq(
        "a causal pair may point both ways",
        len(mod.build_graph(TEXT, (ok, b), (e("a", "b", mod.EdgeKind.CAUSAL), e("b", "a", mod.EdgeKind.CAUSAL))).edges),
        2,
    )
    d = n("d", t.OBSERVATION, spans=(span("this week"),))
    chain = (e("a", "b", mod.EdgeKind.DEPENDS_ON), e("b", "d", mod.EdgeKind.DEPENDS_ON), e("a", "d", mod.EdgeKind.DEPENDS_ON))
    c.eq("a diamond is not a loop", len(mod.build_graph(TEXT, (ok, b, d), chain).edges), 3)
    return c.bad


def check_weak_premises(mod: types.ModuleType) -> list[str]:
    """A forecast that has not settled, a void one and an opinion are not supported premises: what rests on them rests on something unproven, and the sentence names them when a conclusion needs them. A settled forecast is a premise like any other."""
    c = Case(mod)
    n, e, t = mod.Node, mod.Edge, mod.NodeType
    nodes = (
        n("up", t.FORECAST, spans=(span("SOL will rise 15% this week"),)),
        n("who", t.INTERPRETATION, spans=(span("institutions are entering"),)),
        n("say", t.OPINION, spans=(span("Whales are accumulating SOL"),)),
    )
    g = mod.build_graph(TEXT, nodes, (e("who", "up", mod.EdgeKind.DEPENDS_ON),))
    for label, status in (("not settled yet", "PENDING"), ("void", "VOID")):
        got = mod.propagate(g, {"up": status, "who": "SUPPORTED_BY_EVIDENCE"})
        c.eq(
            f"an interpretation that needs a forecast that is {label} rests on an unproven premise",
            got.statuses["who"],
            "RESTS_ON_UNPROVEN_PREMISE",
        )
        c.eq(f"the forecast keeps its own status ({status})", got.statuses["up"], status)
    settled = mod.propagate(g, {"up": "SETTLED", "who": "SUPPORTED_BY_EVIDENCE"})
    c.eq("a settled forecast is a premise like any other", settled.statuses["who"], "SUPPORTED_BY_EVIDENCE")
    c.eq(
        "and nothing is named as weak",
        settled.sentence,
        "Every part that was checked holds on the evidence checked; no causal claim is marked as supported.",
    )
    pending = mod.propagate(g, {"up": "PENDING", "who": "SUPPORTED_BY_EVIDENCE"})
    c.eq(
        "the sentence names the forecast the interpretation needs",
        pending.sentence,
        "The weakest link is the forecast “SOL will rise 15% this week”: a forecast that has not settled.",
    )
    void = mod.propagate(g, {"up": "VOID", "who": "SUPPORTED_BY_EVIDENCE"})
    c.eq(
        "and a void one in its words",
        void.sentence,
        "The weakest link is the forecast “SOL will rise 15% this week”: void, so it never settled.",
    )
    c.eq(
        "a forecast of its own that is contradicted stays contradicted, and a premise that is contradicted outranks a pending one",
        (
            mod.propagate(g, {"up": "PENDING", "who": "CONTRADICTED"}).statuses["who"],
            mod.propagate(g, {"up": "PENDING", "who": "UNPROVEN"}).statuses["who"],
        ),
        ("CONTRADICTED", "RESTS_ON_UNPROVEN_PREMISE"),
    )
    opinion = mod.build_graph(TEXT, nodes, (e("who", "say", mod.EdgeKind.DEPENDS_ON),))
    got = mod.propagate(opinion, {"say": "OPINION_CONTEXT", "who": "SUPPORTED_BY_EVIDENCE"})
    c.eq("an interpretation that needs an opinion rests on an unproven premise", got.statuses["who"], "RESTS_ON_UNPROVEN_PREMISE")
    c.eq(
        "the opinion is named in its words",
        got.sentence,
        "The weakest link is the opinion “Whales are accumulating SOL”: an opinion, not evidence.",
    )
    alone = mod.build_graph(TEXT, nodes[:1] + nodes[2:], ())
    c.eq(
        "a forecast and an opinion that nothing needs are not a weak link of anything",
        mod.propagate(alone, {"up": "PENDING", "say": "OPINION_CONTEXT"}).sentence,
        "Every part that was checked holds on the evidence checked; no causal claim is marked as supported.",
    )
    return c.bad


def check_propagation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(mod)
    fresh = mod.propagate(g, {})
    c.eq("before any check every own status is UNCHECKED", set(fresh.own.values()), {"UNCHECKED"})
    c.eq("a node that needs nothing keeps its own status", fresh.statuses["obs"], "UNCHECKED")
    c.eq("a dependant of an unchecked premise rests on it", fresh.statuses["who"], "RESTS_ON_UNPROVEN_PREMISE")
    c.eq("and so does its dependant", fresh.statuses["up"], "RESTS_ON_UNPROVEN_PREMISE")
    c.eq(
        "the weakest link of a fresh graph is the first unchecked one",
        fresh.sentence,
        "The weakest link is the observation “Whales are accumulating SOL”: not checked yet.",
    )
    ladder = mod.propagate(g, {"obs": "SUPPORTED", "ident": "UNPROVEN", "why": "MECHANISM_UNTESTED", "who": "UNPROVEN", "up": "PENDING"})
    c.eq("support does not travel", (ladder.statuses["obs"], ladder.statuses["who"]), ("SUPPORTED", "RESTS_ON_UNPROVEN_PREMISE"))
    c.eq("the forecast rests on the unproven step", ladder.statuses["up"], "RESTS_ON_UNPROVEN_PREMISE")
    c.eq("a causal link needs nothing, so a supported premise leaves it as it was", ladder.statuses["why"], "MECHANISM_UNTESTED")
    c.eq(
        "the sentence names the first of the weakest by dependency order",
        ladder.sentence,
        "The weakest link is the assumed step “The large wallets belong to institutions”: unproven.",
    )
    allgood = mod.propagate(
        g,
        {"obs": "SUPPORTED", "ident": "SUPPORTED_BY_EVIDENCE", "who": "SUPPORTED_BY_EVIDENCE", "why": "ORDER_CONSISTENT", "up": "PENDING"},
    )
    c.eq(
        "nothing weak: support is not propagated, each keeps its own",
        dict(allgood.statuses),
        {"obs": "SUPPORTED", "ident": "SUPPORTED_BY_EVIDENCE", "who": "SUPPORTED_BY_EVIDENCE", "why": "ORDER_CONSISTENT", "up": "PENDING"},
    )
    c.eq(
        "a graph with nothing weak says so without claiming cause",
        allgood.sentence,
        "Every part that was checked holds on the evidence checked; no causal claim is marked as supported.",
    )
    bad = mod.propagate(g, {"obs": "CONTRADICTED", "ident": "UNPROVEN", "who": "UNPROVEN", "up": "PENDING", "why": "MECHANISM_UNTESTED"})
    c.eq(
        "a contradicted premise marks every dependant",
        (bad.statuses["who"], bad.statuses["up"]),
        ("PREMISE_CONTRADICTED", "PREMISE_CONTRADICTED"),
    )
    c.eq("the unrelated causal link is untouched", bad.statuses["why"], "MECHANISM_UNTESTED")
    c.eq(
        "a contradicted root outranks an unproven one in the sentence",
        bad.sentence,
        "The weakest link is the observation “Whales are accumulating SOL”: contradicted by the evidence checked.",
    )
    both = mod.propagate(g, {"obs": "SUPPORTED", "ident": "CONTRADICTED"})
    c.eq("contradiction wins over unproven among premises", both.statuses["who"], "PREMISE_CONTRADICTED")
    own_bad = mod.propagate(g, {"who": "CONTRADICTED"})
    c.eq("a part that does not hold stays so even though its own premises are unchecked", own_bad.statuses["who"], "CONTRADICTED")
    c.eq("and its dependant falls with it", own_bad.statuses["up"], "PREMISE_CONTRADICTED")
    both_bad = mod.propagate(g, {"obs": "CONTRADICTED", "who": "CONTRADICTED"})
    c.eq("a contradicted premise outranks the part's own contradiction", both_bad.statuses["who"], "PREMISE_CONTRADICTED")
    pair = mod.build_graph(
        TEXT,
        (
            mod.Node("obs2", mod.NodeType.OBSERVATION, spans=(span("Whales are accumulating SOL"),)),
            mod.Node("who2", mod.NodeType.INTERPRETATION, spans=(span("institutions are entering"),)),
        ),
        (mod.Edge("who2", "obs2", mod.EdgeKind.DEPENDS_ON),),
    )
    leaf = mod.propagate(pair, {"obs2": "SUPPORTED", "who2": "CONTRADICTED"})
    c.eq(
        "a conclusion that nothing builds on but that was checked and does not hold is named, never reported as holding",
        leaf.sentence,
        "The weakest link is the interpretation “institutions are entering”: contradicted by the evidence checked.",
    )
    order_own = mod.propagate(g, {"why": "ORDER_INCONSISTENT"})
    c.eq("a causal link whose order fails stays so", order_own.statuses["why"], "ORDER_INCONSISTENT")
    mixed = mod.propagate(g, {"obs": "MIXED", "ident": "SUPPORTED_BY_EVIDENCE"})
    c.eq("a mixed premise is weak, not contradicted", mixed.statuses["who"], "RESTS_ON_UNPROVEN_PREMISE")
    c.eq("a mixed root is worded as mixed", mixed.sentence.endswith("backed by some sources and contradicted by others."), True)
    order_bad = mod.propagate(
        g,
        {
            "obs": "SUPPORTED",
            "why": "ORDER_INCONSISTENT",
            "ident": "SUPPORTED_BY_EVIDENCE",
            "who": "SUPPORTED_BY_EVIDENCE",
            "up": "PENDING",
        },
    )
    c.eq(
        "an inconsistent order is the weakest link",
        order_bad.sentence,
        "The weakest link is the causal link “therefore”: not in the order the claim states.",
    )
    n, e, t = mod.Node, mod.Edge, mod.NodeType
    cond = mod.build_graph(
        TEXT,
        (n("c", t.CONDITION, spans=span("this week") and (span("this week"),)), n("f", t.FORECAST, spans=(span("SOL will rise 15%"),))),
        (e("f", "c", mod.EdgeKind.CONDITIONAL_ON),),
    )
    c.eq("a conditional dependency propagates", mod.propagate(cond, {"c": "CONTRADICTED"}).statuses["f"], "PREMISE_CONTRADICTED")
    long = mod.build_graph(TEXT, (n("l", t.OBSERVATION, spans=((0, len(TEXT)),)),))
    c.eq(
        "a long quotation is cut to sixty characters",
        mod.propagate(long, {"l": "UNRESOLVED"}).sentence,
        "The weakest link is the observation “" + TEXT[:57].rstrip() + "...”: not resolved.",
    )
    sixty = mod.build_graph("x" * 60, (n("l", t.OBSERVATION, spans=((0, 60),)),))
    c.eq(
        "exactly sixty characters is not cut",
        mod.propagate(sixty, {"l": "UNRESOLVED"}).sentence,
        "The weakest link is the observation “" + "x" * 60 + "”: not resolved.",
    )
    c.refuses("check for no node", lambda: mod.propagate(g, {"zz": "SUPPORTED"}), "a check was given for 'zz', which is not a node")
    c.refuses("a status of another type", lambda: mod.propagate(g, {"obs": "PENDING"}), "obs: PENDING is not a status of OBSERVATION")
    c.refuses("UNCHECKED is not a result", lambda: mod.propagate(g, {"obs": "UNCHECKED"}), "obs: UNCHECKED is not a status of OBSERVATION")
    return c.bad


def check_vocabulary(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    t = mod.NodeType
    c.eq(
        "node types",
        [m.value for m in t],
        [
            "OBSERVATION",
            "INTERPRETATION",
            "CAUSAL_LINK",
            "FORECAST",
            "CONDITION",
            "OPINION",
            "PROMOTION_SIGNAL",
            "CORRECTION_REF",
            "ASSUMPTION_INFERRED",
        ],
    )
    c.eq("edge kinds", [m.value for m in mod.EdgeKind], ["DEPENDS_ON", "CAUSAL", "CONDITIONAL_ON"])
    c.eq(
        "statuses by type",
        {k.value: list(v) for k, v in mod.STATUSES.items()},
        {
            "OBSERVATION": [
                "SUPPORTED",
                "CONTRADICTED",
                "MIXED",
                "NOT_FOUND_IN_SOURCES_CHECKED",
                "NOT_CHECKABLE",
                "UNRESOLVED",
                "SOURCE_UNAVAILABLE",
            ],
            "INTERPRETATION": ["SUPPORTED_BY_EVIDENCE", "CONTRADICTED", "UNPROVEN"],
            "CAUSAL_LINK": ["MECHANISM_UNTESTED", "ORDER_CONSISTENT", "ORDER_INCONSISTENT"],
            "FORECAST": ["PENDING", "SETTLED", "VOID", "UNRESOLVED"],
            "CONDITION": ["SUPPORTED", "CONTRADICTED", "UNRESOLVED"],
            "OPINION": ["OPINION_CONTEXT"],
            "PROMOTION_SIGNAL": ["DISCLOSURE_FOUND", "NOT_FOUND", "UNKNOWN"],
            "CORRECTION_REF": ["APPLIES", "DOES_NOT_APPLY"],
            "ASSUMPTION_INFERRED": ["UNPROVEN", "SUPPORTED_BY_EVIDENCE", "CONTRADICTED"],
        },
    )
    c.eq(
        "how each status reads and how weak it is",
        dict(mod.WORDS),
        {
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
            "UNCHECKED": ("not checked yet", 3),
        },
    )
    c.eq("the premises that are only so in name", sorted(mod.UNSUPPORTED_PREMISES), ["OPINION_CONTEXT", "PENDING", "VOID"])
    c.eq("and each of them is weak", mod.UNSUPPORTED_PREMISES <= mod.WEAK_SET, True)
    c.eq(
        "type words",
        {k.value: v for k, v in mod.TYPE_WORDS.items()},
        {
            "OBSERVATION": "the observation",
            "INTERPRETATION": "the interpretation",
            "CAUSAL_LINK": "the causal link",
            "FORECAST": "the forecast",
            "CONDITION": "the condition",
            "OPINION": "the opinion",
            "PROMOTION_SIGNAL": "the promotion signal",
            "CORRECTION_REF": "the correction",
            "ASSUMPTION_INFERRED": "the assumed step",
        },
    )
    c.eq(
        "the propagated statuses",
        (mod.UNCHECKED, mod.PREMISE_CONTRADICTED, mod.RESTS_ON_UNPROVEN_PREMISE),
        ("UNCHECKED", "PREMISE_CONTRADICTED", "RESTS_ON_UNPROVEN_PREMISE"),
    )
    c.eq("the contradicted set", sorted(mod.CONTRADICTED_SET), ["CONTRADICTED", "ORDER_INCONSISTENT", "PREMISE_CONTRADICTED"])
    c.eq(
        "the weak set",
        sorted(mod.WEAK_SET),
        sorted(
            [
                "MIXED",
                "UNPROVEN",
                "NOT_FOUND_IN_SOURCES_CHECKED",
                "NOT_CHECKABLE",
                "UNRESOLVED",
                "SOURCE_UNAVAILABLE",
                "MECHANISM_UNTESTED",
                "UNKNOWN",
                "NOT_FOUND",
                "UNCHECKED",
                "RESTS_ON_UNPROVEN_PREMISE",
                "PENDING",
                "VOID",
                "OPINION_CONTEXT",
            ]
        ),
    )
    for cls in (mod.Node, mod.Edge, mod.Graph, mod.Result):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_more_graphs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    n, e, t = mod.Node, mod.Edge, mod.NodeType
    nodes = tuple(n(x, t.OBSERVATION, spans=(span(w),)) for x, w in (("a", "Whales"), ("b", "institutions"), ("c", "this week")))
    dep = mod.EdgeKind.DEPENDS_ON
    c.refuses(
        "a loop that does not touch the first node",
        lambda: mod.build_graph(TEXT, nodes, (e("a", "b", dep), e("b", "c", dep), e("c", "b", dep))),
        "the dependencies form a loop",
    )
    c.refuses(
        "a three-node loop",
        lambda: mod.build_graph(TEXT, nodes, (e("a", "b", dep), e("b", "c", dep), e("c", "a", dep))),
        "the dependencies form a loop",
    )
    c.eq("a chain is not a loop", len(mod.build_graph(TEXT, nodes, (e("a", "b", dep), e("b", "c", dep))).edges), 2)
    g = mod.build_graph(TEXT, nodes, (e("a", "b", dep), e("b", "c", dep)))
    c.eq("order of a chain", mod.order(g), ("c", "b", "a"))
    link = n("k", t.CAUSAL_LINK, spans=(span("therefore"),))
    top = n("f", t.FORECAST, spans=(span("SOL will rise 15% this week"),))
    needs_link = mod.build_graph(TEXT, (link, top), (e("f", "k", dep),))
    c.eq(
        "a premise whose order fails contradicts its dependants",
        mod.propagate(needs_link, {"k": "ORDER_INCONSISTENT"}).statuses["f"],
        "PREMISE_CONTRADICTED",
    )
    c.eq(
        "a premise whose order holds does not weaken them", mod.propagate(needs_link, {"k": "ORDER_CONSISTENT"}).statuses["f"], "UNCHECKED"
    )
    c.eq("an untested mechanism does", mod.propagate(needs_link, {"k": "MECHANISM_UNTESTED"}).statuses["f"], "RESTS_ON_UNPROVEN_PREMISE")
    s61 = mod.build_graph("x" * 61, (n("l", t.OBSERVATION, spans=((0, 61),)),))
    c.eq(
        "sixty-one characters is cut",
        mod.propagate(s61, {"l": "UNRESOLVED"}).sentence,
        "The weakest link is the observation “" + "x" * 57 + "...”: not resolved.",
    )
    c.eq("a tab is allowed in a claim", len(mod.build_graph("a\tb", (n("a", t.OBSERVATION, spans=((0, 1),)),)).nodes), 1)
    c.refuses(
        "a bell is not",
        lambda: mod.build_graph("a\x07b", (n("a", t.OBSERVATION, spans=((0, 1),)),)),
        "the claim text has a control character",
    )
    return c.bad


def check_properties(mod: types.ModuleType) -> list[str]:
    """No status depends on a node it does not require; adding a supported node changes no other node's status."""
    c = Case(mod)
    g = golden(mod)
    base_checks = {"obs": "SUPPORTED", "ident": "UNPROVEN", "who": "UNPROVEN", "up": "PENDING", "why": "MECHANISM_UNTESTED"}
    base = mod.propagate(g, base_checks).statuses
    for flipped in ("up", "why"):  # nothing requires these, so changing them changes nothing else
        other = dict(base_checks, **{flipped: "UNRESOLVED" if flipped == "up" else "ORDER_INCONSISTENT"})
        out = mod.propagate(g, other).statuses
        c.eq(
            f"changing {flipped} changes nothing it is not under",
            {k: v for k, v in out.items() if k != flipped},
            {k: v for k, v in base.items() if k != flipped},
        )
    extra = mod.Node("z", mod.NodeType.OBSERVATION, spans=(span("this week"),))
    bigger = mod.build_graph(TEXT, (*g.nodes, extra), g.edges)
    after = mod.propagate(bigger, dict(base_checks, z="SUPPORTED")).statuses
    c.eq("adding a supported node changes no other node", {k: v for k, v in after.items() if k != "z"}, dict(base))
    return c.bad


NUMBER_WORDS = (
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
BIDI = (0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069)


def check_numbers_read(mod: types.ModuleType) -> list[str]:
    """A number is read without a comma or a full stop that follows it; a comma between digits stays part of it, as it always was."""
    c = Case(mod)
    hold = "SOL will hold $150 this week"
    c.accepts("a comma after a number is not part of it", lambda: inferred(mod, hold, hold, "At 150, buyers outnumber sellers"))
    c.accepts("nor is a full stop that ends a sentence", lambda: inferred(mod, hold, hold, "Buyers defend 150."))
    c.refuses("a number followed by a comma is still read", lambda: inferred(mod, hold, hold, "At 160, buyers give up"), lacks("160"))
    c.refuses("a decimal is one number", lambda: inferred(mod, hold, hold, "Buyers defend 150.5"), lacks("150.5"))
    wallets = "Over 1,500 wallets bought SOL"
    c.accepts("a comma between digits is part of the number", lambda: inferred(mod, wallets, wallets, "Above 1,500, then more holders"))
    c.refuses("so 1,500 and 1500 are written differently", lambda: inferred(mod, wallets, wallets, "Above 1500 holders"), lacks("1500"))
    c.refuses("and 1,500 is one number, not 1 and 500", lambda: inferred(mod, wallets, wallets, "Above 500 holders"), lacks("500"))
    return c.bad


def check_signs(mod: types.ModuleType) -> list[str]:
    """A minus sign written directly before a number is part of it, so an inferred step cannot turn 15 into -15, or -15 into 15."""
    c = Case(mod)
    rise = "SOL will rise 15% this week"
    c.refuses("a minus sign the words do not have", lambda: inferred(mod, rise, rise, "A -15% week would end it"), lacks("-15"))
    c.refuses("the minus sign U+2212", lambda: inferred(mod, rise, rise, "A \u221215% week would end it"), lacks("\u221215"))
    c.refuses("a minus sign at the start", lambda: inferred(mod, rise, rise, "-15% would end it"), lacks("-15"))
    c.refuses("a minus sign after a bracket", lambda: inferred(mod, rise, rise, "A week (-15%) would end it"), lacks("-15"))
    c.accepts("a plus sign leaves the number as it is", lambda: inferred(mod, rise, rise, "A +15% week is large"))
    move = "SOL will move -15% this week"
    c.refuses("a minus sign dropped", lambda: inferred(mod, move, move, "A 15% move is large"), lacks("15"))
    c.accepts("a minus sign kept", lambda: inferred(mod, move, move, "A -15% move is large"))
    span = "SOL will rise 10-15% this week"
    c.accepts("a hyphen after a digit is a range, not a sign", lambda: inferred(mod, span, span, "A rise of up to 15% is large"))
    pct = "SOL will rise 10%-15% this week"
    c.accepts("and so is one after a percent sign", lambda: inferred(mod, pct, pct, "A rise of up to 15% is large"))
    name = "The X-15 upgrade ships this week"
    c.accepts("and one after a letter", lambda: inferred(mod, name, name, "Upgrade 15 is late"))
    return c.bad


def check_words_and_assets(mod: types.ModuleType) -> list[str]:
    """An inferred step brings in no number word and no asset that the words it cites lack (spec, rules from the source study, 2)."""
    c = Case(mod)
    c.eq("the number words", getattr(mod, "NUMBER_WORDS", None), NUMBER_WORDS)
    rise = "SOL will rise 15% this week"
    for word in NUMBER_WORDS:
        c.refuses(f"the number word {word}", lambda word=word: inferred(mod, rise, rise, f"Buyers are {word} times sellers"), lacks(word))
    c.refuses("a number word in capitals", lambda: inferred(mod, rise, rise, "Twenty percent of buyers are new"), lacks("twenty"))
    c.refuses("a number word in a compound", lambda: inferred(mod, rise, rise, "Buyers are twenty-two times sellers"), lacks("twenty"))
    words = "SOL will rise fifteen percent this week"
    c.accepts("a number word the words contain", lambda: inferred(mod, words, words, "A fifteen percent week is large"))
    c.refuses("numbers are compared as written: digits for a word", lambda: inferred(mod, words, words, "A 15% week is large"), lacks("15"))
    c.refuses("and a word for digits", lambda: inferred(mod, rise, rise, "A fifteen percent week is large"), lacks("fifteen"))
    c.accepts("one is not read, because it is also a pronoun", lambda: inferred(mod, rise, rise, "No one sells in a 15% week"))
    c.accepts("a number word inside another word is not one", lambda: inferred(mod, rise, rise, "Buyers often come back in a 15% week"))
    claim = "Whales are accumulating SOL, therefore institutions are entering."
    who, obs, symbols = "institutions are entering", "Whales are accumulating SOL", ("SOL", "ETH")
    c.refuses("an asset", lambda: inferred(mod, claim, who, "The same institutions are buying ETH", symbols), lacks("ETH"))
    c.refuses(
        "an asset the claim names, but not the words the step cites",
        lambda: inferred(mod, claim, who, "These institutions are buying SOL", symbols),
        lacks("SOL"),
    )
    c.refuses(
        "a symbol before an apostrophe", lambda: inferred(mod, claim, who, "ETH's holders are these institutions", symbols), lacks("ETH")
    )
    c.accepts("an asset the words name", lambda: inferred(mod, claim, obs, "The SOL whales are institutions", symbols))
    c.accepts("with no symbols given, no word is an asset", lambda: inferred(mod, claim, who, "The same institutions are buying ETH"))
    c.accepts("a word in capitals that is not a symbol", lambda: inferred(mod, claim, who, "The ETF lets these institutions in", symbols))
    c.accepts("a symbol not written in capitals", lambda: inferred(mod, claim, who, "These institutions buy eth too", symbols))
    c.refuses(
        "what it brings in is named in the order of its words",
        lambda: inferred(mod, claim, who, "ETH holders bought 20 percent", symbols),
        lacks("ETH"),
    )
    c.refuses("whichever comes first", lambda: inferred(mod, claim, who, "They bought 20 percent of ETH", symbols), lacks("20"))
    return c.bad


def check_bidi_controls(mod: types.ModuleType) -> list[str]:
    """Every character with the Unicode property Bidi_Control is refused, so that a claim shows its words in the order it holds them."""
    c = Case(mod)
    says = "the claim text has a bidirectional control character"
    c.eq("the bidirectional controls", [f"U+{ord(x):04X}" for x in getattr(mod, "BIDI_CONTROLS", "")], [f"U+{x:04X}" for x in BIDI])
    for code in BIDI:
        c.refuses(f"U+{code:04X}", lambda code=code: mod.check_text(f"BTC falls{chr(code)} 99% today"), says)
    for code in (0x061B, 0x061D, 0x200D, 0x2010, 0x2029, 0x202F, 0x7E):
        c.accepts(f"U+{code:04X} is not a bidirectional control", lambda code=code: mod.check_text(f"BTC falls{chr(code)} 99% today"))
    c.refuses(
        "U+2065 is not one either, and is refused because Unicode 15.0 does not assign it",
        lambda: mod.check_text("BTC falls\u2065 99% today"),
        "the claim text is not valid canonical text: code point U+2065 is not assigned in Unicode 15.0",
    )
    n, t = mod.Node, mod.NodeType
    c.refuses(
        "nor can a graph be built on such a text", lambda: mod.build_graph("a\u2067b", (n("a", t.OBSERVATION, spans=((0, 1),)),)), says
    )
    c.refuses("a control character is named first", lambda: mod.check_text("a\x85\u202eb"), "the claim text has a control character")
    return c.bad


def check_canonical_text(mod: types.ModuleType) -> list[str]:
    """A claim's text is valid canonical text, so that whatever is made of it (a request and its digest, a record) can hold it: the
    canonical module is asked, so its rule applies in its own words (no lone surrogate, only code points assigned in Unicode 15.0). The
    rules above it keep their own words and come first."""
    from hoshitori import canonical, typed

    c = Case(mod)
    after = "SOL will rise 15% \U0001fae9 this week"
    says = "the claim text is not valid canonical text: code point U+1FAE9 is not assigned in Unicode 15.0"
    c.refuses("a code point assigned only after Unicode 15.0", lambda: mod.check_text(after), says)
    c.refuses(
        "the words are the canonical module's",
        lambda: mod.check_text(after),
        f"the claim text is not valid canonical text: {typed.not_canonical(after)}",
    )
    c.refuses(
        "a lone surrogate", lambda: mod.check_text("SOL \ud800 will rise"), "the claim text is not valid canonical text: lone surrogate"
    )
    n, t = mod.Node, mod.NodeType
    c.refuses("nor can a graph be built on such a text", lambda: mod.build_graph(after, (n("a", t.OBSERVATION, spans=((0, 3),)),)), says)
    c.accepts("a code point assigned in Unicode 15.0", lambda: mod.check_text("SOL will rise 15% \U0001fae8 this week"))
    c.refuses(
        "a text that is not NFC is named as such first", lambda: mod.check_text("e\u0301\U0001fae9"), "the claim text is not in NFC form"
    )
    c.refuses("so is a control character", lambda: mod.check_text("a\x00\U0001fae9"), "the claim text has a control character")
    c.refuses(
        "and a bidirectional control", lambda: mod.check_text("a\u202e\U0001fae9"), "the claim text has a bidirectional control character"
    )
    for text in (TEXT, "a\n\tb", "x" * 4000, "SOL \U0001fae8 \u00e9"):
        try:
            mod.check_text(text)
            canonical.dumps({"text": text})
        except Exception as exc:
            c.bad.append(f"a text check_text accepts, {text[:20]!r}, is one a record holds: {type(exc).__name__}: {exc}")
    return c.bad


def check_hidden_characters(mod: types.ModuleType) -> list[str]:
    """A claim's text may not hold a character that shows nothing (a format character such as a zero-width space, a word joiner, a soft
    hyphen, a byte-order mark or a Tag character, or a private-use one), which can carry words the person cannot see; the joiners that
    scripts and emoji need (U+200C and U+200D) stay. A stated node needs at least one visible character."""
    c = Case(mod)
    says = "the claim text has a hidden formatting character"
    for code in (0x200B, 0x2060, 0xFEFF, 0x00AD, 0xE0041, 0xE0001, 0xE000, 0x180E, 0x2062):
        c.refuses(f"U+{code:04X}", lambda code=code: mod.check_text(f"BTC falls{chr(code)} 99% today"), says)
    for code in (0x200C, 0x200D, 0x3042, 0x1F680):
        c.accepts(f"U+{code:04X} is allowed", lambda code=code: mod.check_text(f"BTC falls{chr(code)} 99% today"))
    n, t = mod.Node, mod.NodeType
    c.refuses(
        "a stated node of spaces only",
        lambda: mod.build_graph("BTC   falls", (n("a", t.OBSERVATION, spans=((3, 6),)),)),
        "a: a stated node has at least one visible character",
    )
    c.accepts(
        "a node whose spans are spaces and one visible word has a visible character",
        lambda: mod.build_graph("BTC   falls", (n("a", t.OBSERVATION, spans=((3, 6), (6, 11))),)),
    )
    c.refuses(
        "a hidden character is named after the bidirectional ones",
        lambda: mod.check_text("a\u202e\u200bb"),
        "the claim text has a bidirectional control character",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks = (
        check_golden_graph,
        check_refusals,
        check_propagation,
        check_weak_premises,
        check_properties,
        check_vocabulary,
        check_more_graphs,
        check_numbers_read,
        check_signs,
        check_words_and_assets,
        check_bidi_controls,
        check_canonical_text,
        check_hidden_characters,
    )
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
