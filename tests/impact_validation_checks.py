"""Scenario checks of ``hoshitori.impact_validation`` with hand-computed numbers, runnable against a mutant."""

from __future__ import annotations

import math
import types
from collections.abc import Callable

from hoshitori.today_items import TodayItem


class Check:
    def __init__(self) -> None:
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def near(self, label: str, got: float | None, want: float | None) -> None:
        if got is None or want is None:
            if got is not want:
                self.bad.append(f"{label}: got {got!r}, expected {want!r}")
        elif not math.isclose(got, want, abs_tol=1e-9):
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def raises(self, label: str, call: Callable[[], object], error: type[BaseException]) -> None:
        try:
            call()
        except error:
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}, expected {error.__name__}")
            return
        self.bad.append(f"{label}: raised nothing")


def cand(
    mod: types.ModuleType,
    ident: str,
    state: str,
    grades: tuple[int, ...],
    arrival: int,
    price: float | None,
    mrank: int,
    cls: str,
    *,
    contradicting: int = 0,
    read: int = 0,
    symbol: str | None = None,
) -> object:
    item = TodayItem(ident, state, symbol or ident.upper(), "EVENT", "h", None, None, None, "u", None, contradicting, read)
    return mod.Candidate(item, arrival, price, mrank, cls, grades)


def case_a(
    mod: types.ModuleType,
    kind: str = "FIXTURE",
    source: str = "",
    undiscovered: int = 0,
    name: str = "A",
    pair: bool = False,
    flipped: bool = False,
) -> object:
    def g(first: int, second: int) -> tuple[int, ...]:
        second = 3 - second if flipped else second  # a second reviewer who disagrees about what matters
        return (first, second) if pair else (first,)

    cs = (
        cand(mod, "a", "INVESTIGATED_THESIS", g(3, 2), 1, 0.01, 3, "X", contradicting=2),
        cand(mod, "b", "BREAKING_UNVERIFIED", g(0, 0), 4, 0.20, 1, "Y"),
        cand(mod, "c", "NO_RELIABLE_CONCLUSION", g(2, 1), 3, 0.05, 2, "Z", read=1),
        cand(mod, "d", "NO_RELIABLE_CONCLUSION", g(1, 1), 2, None, 4, "Y"),
    )
    return mod.Case(name, kind, source, cs, undiscovered)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Check()
    c.eq("kinds", (mod.FIXTURE, mod.PILOT, mod.KINDS), ("FIXTURE", "PILOT", ("FIXTURE", "PILOT")))
    c.eq("targets", (mod.PILOT_TARGET, mod.CONSEQUENTIAL, mod.MAX_GRADE, mod.KS), (30, 2, 3, (1, 3, 5)))
    c.eq("orders", (mod.RANKERS, tuple(mod.ORDERS)), (("IMPACT", "CHRONOLOGICAL", "PRICE_MOVE", "MANIFEST"),) * 2)
    return c.bad


def check_orders(mod: types.ModuleType) -> list[str]:
    c = Check()
    a = case_a(mod)
    c.eq("impact", mod.rank_impact(a), ["a", "b", "c", "d"])
    c.eq("chronological is newest first", mod.rank_chronological(a), ["b", "c", "d", "a"])
    c.eq("price move is largest first, no price last", mod.rank_price_move(a), ["b", "c", "a", "d"])
    c.eq("manifest order", mod.rank_manifest(a), ["b", "c", "a", "d"])
    c.eq("the table of orders", {k: f(a) for k, f in mod.ORDERS.items()}["MANIFEST"], ["b", "c", "a", "d"])
    return c.bad


def check_metrics(mod: types.ModuleType) -> list[str]:
    c = Check()
    a = case_a(mod)
    imp, chron = mod.rank_impact(a), mod.rank_chronological(a)
    c.near("precision@1 impact", mod.precision_at_k(a, imp, 1), 1.0)
    c.near("precision@1 chronological", mod.precision_at_k(a, chron, 1), 0.0)
    c.near("precision@3 impact", mod.precision_at_k(a, imp, 3), 2 / 3)
    c.near("precision@3 chronological", mod.precision_at_k(a, chron, 3), 1 / 3)
    ideal = 7 + 3 / math.log2(3) + 1 / 2
    c.near("ndcg@3 impact", mod.ndcg_at_k(a, imp, 3), (7 + 0 + 3 / 2) / ideal)
    c.near("ndcg@4 of the ideal order is one", mod.ndcg_at_k(a, ["a", "c", "d", "b"], 4), 1.0)
    c.near("ndcg@1 counts only the first place", mod.ndcg_at_k(a, chron, 1), 0.0)
    c.near("subtopic recall@1", mod.subtopic_recall_at_k(a, imp, 1), 0.5)
    c.near("subtopic recall@3", mod.subtopic_recall_at_k(a, imp, 3), 1.0)
    c.near("subtopic recall counts a class once", mod.subtopic_recall_at_k(a, ["a", "d", "c", "b"], 2), 0.5)
    c.eq("worst missed rank, impact", mod.worst_missed_rank(a, imp), 3)
    c.eq("worst missed rank, chronological", mod.worst_missed_rank(a, chron), 4)
    flat = mod.Case("F", "FIXTURE", "", (cand(mod, "z", "NO_RELIABLE_CONCLUSION", (0,), 1, None, 1, "X"),))
    c.eq("no consequential candidate: no worst rank", mod.worst_missed_rank(flat, ["z"]), None)
    c.eq("no consequential candidate: no subtopic recall", mod.subtopic_recall_at_k(flat, ["z"], 1), None)
    c.near("ndcg of a case with nothing to find is zero", mod.ndcg_at_k(flat, ["z"], 1), 0.0)
    c.near("discovery recall", mod.discovery_recall(case_a(mod, undiscovered=2)), 0.5)
    c.near("discovery recall with nothing missed", mod.discovery_recall(case_a(mod)), 1.0)
    c.eq("discovery recall with nothing to find", mod.discovery_recall(flat), None)
    return c.bad


def case_b(mod: types.ModuleType, name: str = "B") -> object:
    """The ranking puts an unchecked, unimportant development first and the manifest puts the important one first: the ranking loses."""
    return mod.Case(
        name,
        "FIXTURE",
        "",
        (
            cand(mod, "x", "BREAKING_UNVERIFIED", (0,), 2, 0.1, 2, "X"),
            cand(mod, "y", "NO_RELIABLE_CONCLUSION", (3,), 1, 0.2, 1, "Y"),
        ),
    )


def case_c(mod: types.ModuleType, name: str = "C") -> object:
    """The ranking's order has grades 3, 2, 0, 1 and the manifest's 3, 1, 2, 0: the manifest is better at k=3 and worse at k=4."""
    return mod.Case(
        name,
        "FIXTURE",
        "",
        (
            cand(mod, "p", "INVESTIGATED_THESIS", (3,), 1, None, 1, "X", contradicting=2),
            cand(mod, "q", "NO_RELIABLE_CONCLUSION", (2,), 2, None, 3, "Y", read=2),
            cand(mod, "r", "NO_RELIABLE_CONCLUSION", (0,), 3, None, 4, "Z", read=1),
            cand(mod, "s", "NO_RELIABLE_CONCLUSION", (1,), 4, None, 2, "W"),
        ),
    )


def check_agreement(mod: types.ModuleType) -> list[str]:
    c = Check()
    n, observed, kappa = mod.reviewer_agreement([case_a(mod, "PILOT", "src", pair=True)])
    c.eq("pairs", n, 4)
    c.near("observed", observed, 0.75)
    c.near("kappa", kappa, 0.5)
    c.eq("no second reviewer, no agreement", mod.reviewer_agreement([case_a(mod)]), (0, None, None))
    same = mod.Case("S", "PILOT", "s", (cand(mod, "q", "BREAKING_UNVERIFIED", (3, 3), 1, None, 1, "X"),))
    c.eq("all alike: kappa is undefined, not one", mod.reviewer_agreement([same]), (1, 1.0, None))
    c.near("sign test, 3 and 0", mod.sign_test_p(3, 0), 0.25)
    c.near("sign test, 0 and 3", mod.sign_test_p(0, 3), 0.25)
    c.near("sign test, 5 and 1", mod.sign_test_p(5, 1), 0.21875)
    c.near("sign test, nothing differs", mod.sign_test_p(0, 0), 1.0)
    c.near("sign test, a tie of 1 and 1 is capped at one", mod.sign_test_p(1, 1), 1.0)
    return c.bad


def check_case_rules(mod: types.ModuleType) -> list[str]:
    c = Check()
    err = mod.ValidationError
    c.eq("a fixture is fine", mod.check_case(case_a(mod)), None)
    try:
        case_a(mod).kind = "PILOT"  # type: ignore[attr-defined]
        c.bad.append("a case can be changed after it is made")
    except Exception:  # noqa: S110 - the frozen dataclass refuses, which is the point
        pass
    try:
        case_a(mod).candidates[0].arrival = 9  # type: ignore[attr-defined]
        c.bad.append("a candidate can be changed after it is made")
    except Exception:  # noqa: S110
        pass
    mixed = mod.Case("M", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (1, 4), 1, None, 1, "X"),))
    c.raises("one bad grade among good ones", lambda: mod.check_case(mixed), err)
    c.eq("a pilot with a source and two reviewers is fine", mod.check_case(case_a(mod, "PILOT", "https://example.org/a", pair=True)), None)
    c.raises("a pilot without a source", lambda: mod.check_case(case_a(mod, "PILOT", "", pair=True)), err)
    c.raises("a pilot candidate with one reviewer", lambda: mod.check_case(case_a(mod, "PILOT", "src")), err)
    c.raises("another kind", lambda: mod.check_case(case_a(mod, "REAL")), err)
    c.raises("no candidates", lambda: mod.check_case(mod.Case("E", "FIXTURE", "", ())), err)
    one = cand(mod, "a", "BREAKING_UNVERIFIED", (1,), 1, None, 1, "X")
    c.raises("duplicate ids", lambda: mod.check_case(mod.Case("D", "FIXTURE", "", (one, one))), err)
    c.raises("a negative count of undiscovered", lambda: mod.check_case(case_a(mod, undiscovered=-1)), err)
    c.raises(
        "a grade above the scale",
        lambda: mod.check_case(mod.Case("G", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (4,), 1, None, 1, "X"),))),
        err,
    )
    c.raises(
        "a negative grade",
        lambda: mod.check_case(mod.Case("G", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (-1,), 1, None, 1, "X"),))),
        err,
    )
    c.raises(
        "a boolean is not a grade",
        lambda: mod.check_case(mod.Case("G", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (True,), 1, None, 1, "X"),))),
        err,
    )
    c.raises(
        "a candidate without grades",
        lambda: mod.check_case(mod.Case("G", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (), 1, None, 1, "X"),))),
        err,
    )
    c.eq(
        "the top grade is allowed",
        mod.check_case(mod.Case("G", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (3,), 1, None, 1, "X"),))),
        None,
    )
    c.eq(
        "the bottom grade is allowed",
        mod.check_case(mod.Case("G", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (0,), 1, None, 1, "X"),))),
        None,
    )
    c.raises("evaluate refuses a bad case", lambda: mod.evaluate([case_a(mod, "PILOT", "")]), err)
    c.raises("compare refuses a bad case", lambda: mod.compare([case_a(mod, "PILOT", "")]), err)
    c.raises("report refuses a bad case", lambda: mod.report([case_a(mod, "PILOT", "")]), err)
    c.raises("compare knows two metrics", lambda: mod.compare([case_a(mod)], "recall"), err)
    return c.bad


def check_evaluate(mod: types.ModuleType) -> list[str]:
    c = Check()
    table = mod.evaluate([case_a(mod)])
    c.eq("four orders", sorted(table), sorted(["IMPACT", "CHRONOLOGICAL", "PRICE_MOVE", "MANIFEST"]))
    c.near("impact precision@1", table["IMPACT"]["precision@1"], 1.0)
    c.near("chronological precision@3", table["CHRONOLOGICAL"]["precision@3"], 1 / 3)
    c.near("worst missed rank", table["PRICE_MOVE"]["worst_missed_rank"], 3.0)
    c.near("subtopic recall is averaged over the cases that define it", table["IMPACT"]["subtopic_recall@1"], 0.5)
    two = mod.evaluate([case_a(mod, name="A"), case_a(mod, name="B")])
    c.near("a mean of equal cases", two["IMPACT"]["precision@3"], 2 / 3)
    short = mod.Case("S", "FIXTURE", "", (cand(mod, "a", "BREAKING_UNVERIFIED", (3,), 1, None, 1, "X"),))
    c.near("k is cut to the number of candidates", mod.evaluate([short])["IMPACT"]["precision@5"], 1.0)
    c.near("ndcg@1 impact", table["IMPACT"]["ndcg@1"], 7 / (7))
    c.near("ndcg@3 impact", table["IMPACT"]["ndcg@3"], 8.5 / (7 + 3 / math.log2(3) + 0.5))
    c.near(
        "ndcg@5 impact is cut to the four candidates",
        table["IMPACT"]["ndcg@5"],
        table["IMPACT"]["ndcg@3"] + (1 / 2.321928094887362) / (7 + 3 / math.log2(3) + 0.5),
    )
    lost = mod.compare([case_b(mod)] * 2)
    c.eq("the ranking can lose to the manifest order", lost["MANIFEST"][:3], (0, 2, 0))
    c.eq("and to the price order", lost["PRICE_MOVE"][:3], (0, 2, 0))
    c.near("a sign test of two losses", lost["MANIFEST"][3], 0.5)
    c.eq("the default is nDCG at 3", mod.compare([case_c(mod)])["MANIFEST"][:3], (0, 1, 0))
    c.eq("a longer cut changes the verdict", mod.compare([case_c(mod)], "ndcg", 4)["MANIFEST"][:3], (1, 0, 0))
    got = mod.compare([case_a(mod)] * 3)
    c.eq(
        "impact wins on nDCG@3 against chronological and ties the price and manifest orders only when equal",
        got["CHRONOLOGICAL"][:3],
        (3, 0, 0),
    )
    c.near("sign test of three wins", got["CHRONOLOGICAL"][3], 0.25)
    c.eq("baselines compared", sorted(got), ["CHRONOLOGICAL", "MANIFEST", "PRICE_MOVE"])
    prec = mod.compare([case_a(mod)], "precision", 3)
    c.eq(
        "precision@3: impact 2/3 against price 2/3 is a tie, against chronological a win",
        (prec["PRICE_MOVE"][:3], prec["CHRONOLOGICAL"][:3]),
        ((0, 0, 1), (1, 0, 0)),
    )
    return c.bad


def check_report(mod: types.ModuleType) -> list[str]:
    c = Check()
    none = mod.report([case_a(mod)])
    c.eq("the label leads", none.splitlines()[0], "Provisional research-impact ranking; independent validation pending. Formula impact/1.")
    c.eq("counts", none.splitlines()[1], "Cases: 0 pilot of 30 wanted, 1 fixture. Fixtures test this harness, not the ranking.")
    c.eq("no agreement yet", none.splitlines()[2], "Reviewer agreement on the pilot cases: none yet.")
    c.eq(
        "no comparison from fixtures",
        none.splitlines()[3],
        "No comparison is shown: there are no pilot cases, and fixture cases are not evidence for the ranking.",
    )
    alike = mod.report([mod.Case("S", "PILOT", "s", (cand(mod, "q", "BREAKING_UNVERIFIED", (3, 3), 1, None, 1, "X"),))])
    c.eq(
        "kappa undefined is said so",
        alike.splitlines()[2],
        "Reviewer agreement on the pilot cases: 1.00 raw, kappa undefined over 1 candidates.",
    )
    c.raises("report refuses a bad fixture too", lambda: mod.report([case_a(mod, undiscovered=-1)]), mod.ValidationError)
    one = mod.report([case_a(mod, "PILOT", "src", pair=True)])
    row = next(line for line in one.splitlines() if line.startswith("IMPACT:"))
    keys = [part.split(" ")[0] for part in row[len("IMPACT: ") :].split(", ")]
    c.eq("a row lists its metrics in order", keys, sorted(keys))
    c.eq("and starts with the first of them", row.startswith("IMPACT: ndcg@1 1.00, ndcg@3 0.90, ndcg@5 "), True)
    c.eq("separated by commas", ", precision@1 1.00, " in row, True)
    c.eq(
        "the comparison line",
        "IMPACT against CHRONOLOGICAL on nDCG@3 per case: better 1, worse 0, equal 0, sign test p 1.000." in one,
        True,
    )
    c.eq("agreement is stated", one.splitlines()[2], "Reviewer agreement on the pilot cases: 0.75 raw, kappa 0.50 over 4 candidates.")
    c.eq("a comparison is shown for pilots", any(line.startswith("IMPACT against CHRONOLOGICAL") for line in one.splitlines()), True)
    c.eq("and no claim below the target", one.splitlines()[-1], "Fewer than 30 pilot cases: no ranking claim is made.")
    full = mod.report([case_a(mod, "PILOT", "src", pair=True, name=f"P{n}") for n in range(30)])
    c.eq("at the target the caution goes", "Fewer than" in full, False)
    mixed = mod.report([case_a(mod, pair=True, flipped=True), case_a(mod, "PILOT", "src", pair=True, name="P"), case_b(mod)])
    c.eq("fixtures never enter the counts", "Cases: 1 pilot of 30 wanted, 2 fixture." in mixed, True)
    c.eq("or the agreement", "0.75 raw, kappa 0.50 over 4 candidates." in mixed, True)
    c.eq("or the comparison", "better 1, worse 0, equal 0" in mixed, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_orders, check_metrics, check_agreement, check_case_rules, check_evaluate, check_report):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
