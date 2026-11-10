"""Checks of the stability of a claim's decomposition (``hoshitori.claim_stability``), runnable against a mutant."""

from __future__ import annotations

import types
from typing import Any

from hoshitori import claim_graph as cg
from hoshitori.claim_segmenter import Disagreement
from tests.claim_graph_checks import TEXT, golden, span

N, T = cg.Node, cg.NodeType


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
        except self.mod.StabilityError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def graph(text: str, *nodes: Any) -> Any:
    return cg.build_graph(text, tuple(nodes))


def check_overlap(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = N("a", T.OBSERVATION, spans=((0, 10),))
    c.eq("identical words", mod.overlap(a, a), 1.0)
    c.eq("half in common out of fifteen", mod.overlap(a, N("b", T.OBSERVATION, spans=((5, 15),))), 5 / 15)
    c.eq("nothing in common", mod.overlap(a, N("b", T.OBSERVATION, spans=((10, 20),))), 0.0)
    c.eq("contained", mod.overlap(a, N("b", T.OBSERVATION, spans=((0, 20),))), 0.5)
    c.eq(
        "several spans",
        mod.overlap(N("m", T.OBSERVATION, spans=((0, 5), (10, 15))), N("n", T.OBSERVATION, spans=((0, 5), (12, 15)))),
        8 / 10,
    )
    c.eq("the same both ways", mod.overlap(N("b", T.OBSERVATION, spans=((0, 20),)), a), 0.5)
    c.eq("the constants", (mod.OVERLAP, mod.THRESHOLD), (0.5, 0.7))
    return c.bad


def check_decompose(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(cg)
    same = mod.decompose((g, g), ())
    c.eq("identical readings", (same.state, same.score, same.unmatched, same.type_conflicts), ("STABLE", 1.0, (), ()))
    c.eq("never validated is said", same.shown(), ("STABLE", "NOT_VALIDATED"))
    c.eq("validated is not said", mod.decompose((g, g), (), True).shown(), ("STABLE",))
    c.eq("the flag is kept", (same.validated, mod.decompose((g, g), (), True).validated), (False, True))
    stated = [x for x in g.nodes if x.type is not T.ASSUMPTION_INFERRED]
    three = cg.build_graph(TEXT, tuple(x for x in stated if x.node_id != "up"))
    d = mod.decompose((g, three), ())
    c.eq("a run that missed one of four nodes", (d.state, d.score, d.unmatched), ("STABLE", 0.75, ("up",)))
    two = cg.build_graph(TEXT, tuple(x for x in stated if x.node_id in ("obs", "who")))
    d = mod.decompose((g, two), ())
    c.eq("a run that missed two of four nodes", (d.state, d.score, d.unmatched), ("UNSTABLE", 0.5, ("why", "up")))
    extra = cg.build_graph(TEXT, (*stated, N("z", T.OBSERVATION, spans=(span("this week"),))))
    d = mod.decompose((g, extra), ())
    c.eq("a run that found a node more lowers agreement", (d.state, d.score, d.unmatched), ("STABLE", 0.8, ()))
    many = cg.build_graph(
        TEXT,
        (
            *stated,
            N("z", T.OBSERVATION, spans=(span("this week"),)),
            N("y", T.OBSERVATION, spans=(span("15%"),)),
            N("x", T.OBSERVATION, spans=(span("rise"),)),
        ),
    )
    c.eq("and the more it found the lower", mod.decompose((g, many), ()).score, 4 / 7)
    c.eq("which can be unstable although nothing was missed", mod.decompose((g, many), ()).state, "UNSTABLE")
    found = (Disagreement("NUMBER_WITHOUT_NODE", 0, 1, "x"),)
    d = mod.decompose((g, g), found, True)
    c.eq("a disagreement of the plain reader disputes a perfect agreement", (d.state, d.score, d.disagreements), ("DISPUTED", 1.0, found))
    c.eq("dispute outranks instability", mod.decompose((g, two), found).state, "DISPUTED")
    # a node found with a different type
    other = cg.build_graph(TEXT, tuple(N(x.node_id, T.OPINION if x.node_id == "who" else x.type, spans=x.spans) for x in stated))
    d = mod.decompose((g, other), ())
    c.eq("a node found as another kind", (d.state, d.score, d.type_conflicts), ("STABLE", 1.0, ("who",)))
    # the boundary of overlap
    t20 = "x" * 30
    first = graph(t20, N("a", T.OBSERVATION, spans=((0, 10),)))
    half = graph(t20, N("a", T.OBSERVATION, spans=((0, 20),)))
    short = graph(t20, N("a", T.OBSERVATION, spans=((0, 21),)))
    c.eq("overlap of exactly one half is the same node", mod.decompose((first, half), ()).unmatched, ())
    c.eq("a little less is not", mod.decompose((first, short), ()).unmatched, ("a",))
    # one node of the other run matches one node of the first, not two
    twin = graph(t20, N("a", T.OBSERVATION, spans=((0, 10),)), N("b", T.OBSERVATION, spans=((0, 10),)))
    only = graph(t20, N("a", T.OBSERVATION, spans=((0, 10),)))
    c.eq("one node found cannot stand for two", mod.decompose((twin, only), ()).unmatched, ("b",))
    c.eq("and is counted against the larger run", mod.decompose((only, twin), ()).score, 0.5)
    # three runs: a node missing from any one counts once
    c.eq("a node missed by one of three runs", mod.decompose((g, g, three), ()).unmatched, ("up",))
    c.eq("a node missed by both others counts once", mod.decompose((g, three, three), ()).unmatched, ("up",))
    c.eq(
        "the reported order is the first run's",
        mod.decompose((g, cg.Graph(TEXT, tuple(x for x in stated if x.node_id == "obs"), ())), ()).unmatched,
        ("who", "why", "up"),
    )
    # inferred nodes are not compared
    withinf = golden(cg)
    c.eq("an inferred node is not compared", mod.decompose((withinf, three), ()).score, 0.75)
    c.eq(
        "the best match is taken, not the first",
        mod.decompose((first, graph(t20, N("p", T.OBSERVATION, spans=((0, 4),)), N("q", T.OBSERVATION, spans=((0, 10),)))), ()).unmatched,
        (),
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(cg)
    c.refuses("one reading", lambda: mod.decompose((g,), ()), "at least two readings are needed to compare")
    c.refuses("no reading", lambda: mod.decompose((), ()), "at least two readings are needed to compare")
    other = graph("abc def", N("a", T.OBSERVATION, spans=((0, 3),)))
    c.refuses("two texts", lambda: mod.decompose((g, other), ()), "the readings are of different texts")
    c.eq("frozen", mod.Decomposition.__dataclass_params__.frozen, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_overlap, check_decompose, check_refusals)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
