"""Checks of what a claim depends on each of its parts (``hoshitori.claim_assumptions``), runnable against a mutant."""

from __future__ import annotations

import itertools
import types
from typing import Any

from hoshitori import claim_graph as cg
from tests.claim_graph_checks import TEXT, golden, span

N, T = cg.Node, cg.NodeType
U = "UNCHECKED"
R = "RESTS_ON_UNPROVEN_PREMISE"
P = "PREMISE_CONTRADICTED"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def by(found: Any) -> dict[tuple[str, str], Any]:
    return {(a.node_id, a.kind): a for a in found}


def check_golden(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(cg)
    found = mod.assumptions(g, {})
    c.eq("a forecast cannot be supposed either way", {a.node_id for a in found}, {"obs", "who", "why", "ident"})
    c.eq("three each", sorted(a.kind for a in found), sorted(["TRUE", "FALSE", "REMOVED"] * 4))
    m = by(found)
    c.eq("a premise supposed to fail brings down what stands on it", m["obs", "FALSE"].changed, (("who", R, P), ("up", R, P)))
    c.eq(
        "and its sentence",
        m["obs", "FALSE"].sentence,
        "If “Whales are accumulating SOL” did not hold, 2 other parts would change. A supposition, not a finding.",
    )
    c.eq("a premise supposed true changes nothing while the others are unchecked", m["obs", "TRUE"].changed, ())
    c.eq(
        "and says so",
        m["obs", "TRUE"].sentence,
        "If “Whales are accumulating SOL” held, no other part would change. A supposition, not a finding.",
    )
    c.eq(
        "taking a premise out leaves the part resting on the other",
        (m["obs", "REMOVED"].changed, m["obs", "REMOVED"].sentence),
        ((), "Without “Whales are accumulating SOL”, no other part would change. A supposition, not a finding."),
    )
    c.eq(
        "a part that does not hold takes its dependant with it",
        (m["who", "FALSE"].changed, m["who", "FALSE"].sentence),
        ((("up", R, P),), "If “institutions are entering” did not hold, 1 other part would change. A supposition, not a finding."),
    )
    c.eq("taking the part out frees what stood on it", m["who", "REMOVED"].changed, (("up", R, U),))
    c.eq("a part nothing stands on changes nothing", [m["why", k].changed for k in ("TRUE", "FALSE", "REMOVED")], [(), (), ()])
    c.eq("an implied step supposed false", m["ident", "FALSE"].changed, (("who", R, P), ("up", R, P)))
    checked = mod.assumptions(g, {"obs": "SUPPORTED", "ident": "SUPPORTED_BY_EVIDENCE"})
    k = by(checked)
    c.eq("once its premises hold, supposing a part true frees what stands on it", k["who", "TRUE"].changed, (("up", R, U),))
    c.eq("and supposing it false still brings it down", k["who", "FALSE"].changed, (("up", R, P),))
    c.eq("a part already checked is replaced by the supposition", k["obs", "FALSE"].changed, (("who", U, P), ("up", R, P)))
    long = cg.build_graph(TEXT, (N("l", T.OBSERVATION, spans=((0, len(TEXT)),)),))
    c.eq(
        "a long part is cut in the sentence",
        mod.assumptions(long, {})[0].sentence,
        "If “" + TEXT[:57].rstrip() + "...” held, no other part would change. A supposition, not a finding.",
    )
    sixty = cg.build_graph("x" * 60, (N("l", T.OBSERVATION, spans=((0, 60),)),))
    c.eq(
        "exactly sixty is not cut",
        mod.assumptions(sixty, {})[0].sentence,
        "If “" + "x" * 60 + "” held, no other part would change. A supposition, not a finding.",
    )
    sixty_one = cg.build_graph("x" * 61, (N("l", T.OBSERVATION, spans=((0, 61),)),))
    c.eq(
        "sixty-one is cut",
        mod.assumptions(sixty_one, {})[0].sentence,
        "If “" + "x" * 57 + "...” held, no other part would change. A supposition, not a finding.",
    )
    c.eq("the short form", (mod.short("abc"), mod.short("y" * 61)), ("abc", "y" * 57 + "..."))
    c.eq(
        "the sentence from its parts",
        mod.sentence_of("FALSE", "w", 2),
        "If “w” did not hold, 2 other parts would change. A supposition, not a finding.",
    )
    one = cg.build_graph(
        TEXT,
        (N("a", T.OBSERVATION, spans=(span("Whales"),)), N("b", T.OBSERVATION, spans=(span("institutions"),))),
        (cg.Edge("b", "a", cg.EdgeKind.DEPENDS_ON),),
    )
    c.eq(
        "one part changes: singular",
        by(mod.assumptions(one, {}))["a", "FALSE"].sentence,
        "If “Whales” did not hold, 1 other part would change. A supposition, not a finding.",
    )
    c.eq(
        "one part removed: singular",
        by(mod.assumptions(one, {}))["a", "REMOVED"].sentence,
        "Without “Whales”, 1 other part would change. A supposition, not a finding.",
    )
    c.eq("which is the dependant, freed", by(mod.assumptions(one, {}))["a", "REMOVED"].changed, (("b", R, U),))
    c.eq("the vocabulary", (mod.TRUE, mod.FALSE, mod.REMOVED), ("TRUE", "FALSE", "REMOVED"))
    c.eq(
        "what can be supposed",
        sorted(t.value for t in mod.HOLDS),
        sorted(["OBSERVATION", "INTERPRETATION", "CONDITION", "CAUSAL_LINK", "ASSUMPTION_INFERRED"]),
    )
    c.eq(
        "holds",
        {t.value: v for t, v in mod.HOLDS.items()},
        {
            "OBSERVATION": "SUPPORTED",
            "INTERPRETATION": "SUPPORTED_BY_EVIDENCE",
            "CONDITION": "SUPPORTED",
            "CAUSAL_LINK": "ORDER_CONSISTENT",
            "ASSUMPTION_INFERRED": "SUPPORTED_BY_EVIDENCE",
        },
    )
    c.eq(
        "fails",
        {t.value: v for t, v in mod.FAILS.items()},
        {
            "OBSERVATION": "CONTRADICTED",
            "INTERPRETATION": "CONTRADICTED",
            "CONDITION": "CONTRADICTED",
            "CAUSAL_LINK": "ORDER_INCONSISTENT",
            "ASSUMPTION_INFERRED": "CONTRADICTED",
        },
    )
    c.eq("frozen", mod.Assumption.__dataclass_params__.frozen, True)
    return c.bad


def check_property(mod: types.ModuleType) -> list[str]:
    """Supposing a part true never creates a contradiction and supposing it false never repairs one, whatever has been checked."""
    c = Case(mod)
    g = golden(cg)
    choices = {
        "obs": (None, "SUPPORTED", "CONTRADICTED"),
        "ident": (None, "SUPPORTED_BY_EVIDENCE", "UNPROVEN"),
        "who": (None, "SUPPORTED_BY_EVIDENCE", "CONTRADICTED"),
    }
    seen = 0
    for combo in itertools.product(*choices.values()):
        checks = {k: v for k, v in zip(choices, combo, strict=True) if v is not None}
        base = cg.propagate(g, checks).statuses
        for a in mod.assumptions(g, checks):
            for node, before, after in a.changed:
                seen += 1
                if a.kind == "TRUE" and after in cg.CONTRADICTED_SET and before not in cg.CONTRADICTED_SET:
                    c.bad.append(f"{checks}: supposing {a.node_id} true made {node} contradicted")
                if a.kind == "FALSE" and before in cg.CONTRADICTED_SET and after not in cg.CONTRADICTED_SET:
                    c.bad.append(f"{checks}: supposing {a.node_id} false repaired {node}")
                if before != base[node]:
                    c.bad.append(f"{checks}: the 'before' of {node} is not its status without the supposition")
    c.eq("enough changes were seen", seen > 20, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_golden, check_property)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
