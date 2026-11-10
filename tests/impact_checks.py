"""Scenario checks of ``hoshitori.impact``, runnable against a mutant: `failures(mod)` returns what went wrong."""

from __future__ import annotations

import types

from hoshitori.today_items import TodayItem


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def item(state: str, symbol: str, item_id: str, *, reason: str | None = None, contradicting: int = 0, read: int = 0) -> TodayItem:
    return TodayItem(item_id, state, symbol, "EVENT", "h", None, reason, None, "u", None, contradicting, read)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("version", mod.FORMULA_VERSION, "impact/1")
    c.eq("label", mod.LABEL, "Provisional research-impact ranking; independent validation pending.")
    c.eq("weights", (mod.PREMISE_CHANGE, mod.CONTRADICTION, mod.NEW_ORIGIN, mod.CONFLICT, mod.UNCHECKED), (10, 6, 2, 4, 3))
    c.eq("caps", dict(mod.CAPS), {"CONTRADICTION": 3, "NEW_ORIGIN": 4})
    c.eq("thresholds", (mod.HIGH, mod.OVERLAP_LIMIT, mod.TOP), (14, 2, 5))
    c.eq(
        "unknown components are listed, not scored",
        mod.UNKNOWN_COMPONENTS,
        ("INVALIDATION_PROXIMITY", "COVERAGE_LOSS", "CORRECTION_OR_RETRACTION", "TRIAGE_MARGIN", "PRACTICE_POSITION_EFFECT"),
    )
    c.eq("components in their fixed order", mod.COMPONENTS, ("PREMISE_CHANGE", "CONTRADICTION", "NEW_ORIGIN", "CONFLICT", "UNCHECKED"))
    c.eq(
        "sentences",
        dict(mod.SENTENCES),
        {
            "PREMISE_CHANGE": "A premise of a recorded call changed.",
            "CONTRADICTION": "Independent origins that contradict the move: {n}.",
            "NEW_ORIGIN": "Independent origins read: {n}.",
            "CONFLICT": "The checks disagreed and the disagreement stayed unresolved.",
            "UNCHECKED": "Nothing about it has been checked yet.",
        },
    )
    c.eq("conflict reason", mod.CONFLICT_REASON, "EVIDENCE_CONFLICT_UNRESOLVED")
    return c.bad


def check_score(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    update = item("MATERIAL_UPDATE", "BTC", "u1")
    c.eq("an update scores its premise change", (mod.score(update), mod.contributions(update)), (10, (("PREMISE_CHANGE", 1, 10),)))
    two = item("INVESTIGATED_THESIS", "BTC", "i1", contradicting=2, read=3)
    c.eq("contradictions and origins", mod.contributions(two), (("CONTRADICTION", 2, 12), ("NEW_ORIGIN", 3, 6)))
    capped = item("INVESTIGATED_THESIS", "BTC", "i2", contradicting=9, read=9)
    c.eq("caps bind", mod.contributions(capped), (("CONTRADICTION", 3, 18), ("NEW_ORIGIN", 4, 8)))
    conflict = item("NO_RELIABLE_CONCLUSION", "BTC", "n1", reason="EVIDENCE_CONFLICT_UNRESOLVED")
    c.eq("an unresolved conflict", (mod.score(conflict), mod.contributions(conflict)), (4, (("CONFLICT", 1, 4),)))
    other = item("NO_RELIABLE_CONCLUSION", "BTC", "n2", reason="ESSENTIAL_PREMISE_UNPROVEN")
    c.eq("another reason scores nothing, and nothing is invented", (mod.score(other), mod.contributions(other)), (0, ()))
    unchecked = item("BREAKING_UNVERIFIED", "BTC", "b1")
    c.eq("not yet checked", (mod.score(unchecked), mod.contributions(unchecked)), (3, (("UNCHECKED", 1, 3),)))
    c.eq("negative counts are not points", mod.score(item("INVESTIGATED_THESIS", "BTC", "z", contradicting=-1, read=-1)) <= 0, True)
    return c.bad


def check_reasons(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    big = item("INVESTIGATED_THESIS", "BTC", "i1", contradicting=2, read=3)
    c.eq(
        "largest contribution first, in words",
        mod.reasons(big),
        ("Independent origins that contradict the move: 2.", "Independent origins read: 3."),
    )
    tie = item("MATERIAL_UPDATE", "BTC", "t", reason="EVIDENCE_CONFLICT_UNRESOLVED", contradicting=0, read=5)
    c.eq("a tie in points keeps the fixed component order", mod.reasons(tie)[0], "A premise of a recorded call changed.")
    wide = item("INVESTIGATED_THESIS", "BTC", "w", contradicting=1, read=4)  # origin 8 outranks contradiction 6
    c.eq(
        "ordered by points, not by the fixed order",
        mod.reasons(wide),
        ("Independent origins read: 4.", "Independent origins that contradict the move: 1."),
    )
    c.eq(
        "conflict and unchecked sentences", mod.reasons(item("BREAKING_UNVERIFIED", "B", "q")), ("Nothing about it has been checked yet.",)
    )
    c.eq(
        "conflict sentence",
        mod.reasons(item("NO_RELIABLE_CONCLUSION", "B", "q", reason="EVIDENCE_CONFLICT_UNRESOLVED")),
        ("The checks disagreed and the disagreement stayed unresolved.",),
    )
    c.eq("no contribution, no reason", mod.reasons(item("INVESTIGATED_THESIS", "BTC", "e")), ())
    scored = mod.scored(big)
    c.eq("scored carries score and reasons", (scored.score, scored.reasons), (mod.score(big), mod.reasons(big)))
    c.eq("scoring leaves the original untouched", (big.score, big.reasons), (0, ()))
    return c.bad


def check_slate(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("nothing in, nothing out", mod.slate(()), ())
    a = item("INVESTIGATED_THESIS", "AAA", "a", contradicting=1)
    b = item("INVESTIGATED_THESIS", "BBB", "b", contradicting=3)
    u1 = item("BREAKING_UNVERIFIED", "CCC", "u1")
    u2 = item("BREAKING_UNVERIFIED", "DDD", "u2", read=1)
    got = [i.item_id for i in mod.slate((a, b, u1, u2))]
    c.eq("featured is the highest-scoring investigated call, then the best unchecked, then the rest", got, ["b", "u2", "a", "u1"])
    same = [i.item_id for i in mod.slate((item("INVESTIGATED_THESIS", "A", "x1"), item("INVESTIGATED_THESIS", "B", "x2")))]
    c.eq("equal scores keep the order they came in", same, ["x1", "x2"])
    upd = item("MATERIAL_UPDATE", "EEE", "up")
    c.eq("an update can be featured", mod.slate((a, upd))[0].item_id, "up")
    only = [i.item_id for i in mod.slate((u1, u2))]
    c.eq("with no investigated call the best unchecked leads the list", only, ["u2", "u1"])
    # overlap: three items about one token in the first five, the third (low score) is deferred behind others
    s1 = item("NO_RELIABLE_CONCLUSION", "SOL", "s1", reason="EVIDENCE_CONFLICT_UNRESOLVED")
    s2 = item("NO_RELIABLE_CONCLUSION", "SOL", "s2", reason="EVIDENCE_CONFLICT_UNRESOLVED")
    s3 = item("NO_RELIABLE_CONCLUSION", "SOL", "s3", reason="EVIDENCE_CONFLICT_UNRESOLVED")
    o1 = item("NO_RELIABLE_CONCLUSION", "ETH", "o1")
    o2 = item("NO_RELIABLE_CONCLUSION", "XRP", "o2")
    got = [i.item_id for i in mod.slate((s1, s2, s3, o1, o2))]
    c.eq("a third item about one token waits behind others", got, ["s1", "s2", "o1", "o2", "s3"])
    # the featured and the reserved rows count towards the limit on one token
    f = item("INVESTIGATED_THESIS", "SOL", "f", contradicting=1)
    r = item("BREAKING_UNVERIFIED", "SOL", "r")
    got = [i.item_id for i in mod.slate((f, r, s3, o1, o2))]
    c.eq("featured and reserved count towards the limit", got, ["f", "r", "o1", "o2", "s3"])
    # the limit applies only inside the first five places
    a1 = item("NO_RELIABLE_CONCLUSION", "SOL", "a1", reason="EVIDENCE_CONFLICT_UNRESOLVED")
    a2 = item("NO_RELIABLE_CONCLUSION", "SOL", "a2", reason="EVIDENCE_CONFLICT_UNRESOLVED")
    a3 = item("NO_RELIABLE_CONCLUSION", "SOL", "a3", read=1)
    t1, t2, t3 = (item("NO_RELIABLE_CONCLUSION", n, n, read=1) for n in ("T1", "T2", "T3"))
    t4 = item("NO_RELIABLE_CONCLUSION", "T4", "T4")
    got = [i.item_id for i in mod.slate((a1, a2, t1, t2, t3, a3, t4))]
    c.eq("after five places the limit no longer defers", got, ["a1", "a2", "T1", "T2", "T3", "a3", "T4"])
    f2 = item("INVESTIGATED_THESIS", "SOL", "f2", read=1)
    r2 = item("BREAKING_UNVERIFIED", "SOL", "r2")
    at_high = item("NO_RELIABLE_CONCLUSION", "SOL", "ah", reason="EVIDENCE_CONFLICT_UNRESOLVED", contradicting=1, read=2)
    c.eq("a score of exactly HIGH", mod.score(at_high), mod.HIGH)
    got = [i.item_id for i in mod.slate((f2, r2, at_high, o1))]
    c.eq("exactly HIGH is exempt from the limit", got, ["f2", "r2", "ah", "o1"])
    one = [i.item_id for i in mod.slate((f2, s1, s2, o1))]
    c.eq("one featured item counts once", one, ["f2", "s1", "o1", "s2"])
    exact = item("INVESTIGATED_THESIS", "SOL", "x", contradicting=2, read=1)  # exactly HIGH
    c.eq("exactly HIGH is exempt", mod.score(exact), mod.HIGH)
    got = [i.item_id for i in mod.slate((s1, s2, exact, o1, o2))]
    c.eq("an item at HIGH stays where its score puts it", got[0], "x")
    got = [
        i.item_id
        for i in mod.slate((s1, s2, item("NO_RELIABLE_CONCLUSION", "SOL", "y", reason="EVIDENCE_CONFLICT_UNRESOLVED", read=4), o1))
    ]
    c.eq("under HIGH the limit still defers", got, ["y", "s1", "o1", "s2"])
    high = item("INVESTIGATED_THESIS", "SOL", "h", contradicting=3, read=4)  # 18 + 8 = 26 >= HIGH
    got = [i.item_id for i in mod.slate((s1, s2, high, o1, o2))]
    c.eq("a high-consequence item is not deferred by the repetition limit", got.index("h") < 3, True)
    c.eq("every input appears once", sorted(got), sorted(["s1", "s2", "h", "o1", "o2"]))
    c.eq("items come back scored", all(i.score == mod.score(i) for i in mod.slate((a, b, u1))), True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_score, check_reasons, check_slate):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
