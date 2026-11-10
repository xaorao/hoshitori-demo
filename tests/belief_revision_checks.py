"""Scenarios for the belief-revision benchmark (``hoshitori.belief_revision``), runnable against the real module or a mutant of it.

Every planted case has a human-adjudicated correct response, and every number below is worked out by hand from the rules of the scripted
answerers (the module's docstring): a primary source weighs 4, a secondary 2 and a low-quality one 0.5; a distinct origin counts once, at its
heaviest card; each unit of weight moves the chance of a rise by 0.05; a retracted card is removed; a small deterministic wobble of at most
half a point depends on the run only.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from decimal import Decimal as Dec
from typing import Any

from hoshitori import card_lint, labels


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def card(mod: types.ModuleType, ident: str, origin: str, quality: str, stance: int, retracts: str | None = None) -> Any:
    return mod.Card(ident, origin, quality, stance, retracts)


def check_the_answerers(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    blog = card(mod, "b1", "blog", "SECONDARY", 1)
    exch = card(mod, "x1", "exchange", "PRIMARY", -1)
    low = card(mod, "l1", "forum", "LOW", -1)
    sound, gull = mod.sound, mod.gullible
    c.eq("nothing known: the chance is half, with the run's wobble (run 1 has none)", sound((), 1), Dec("0.5"))
    c.eq("one secondary source for a rise moves it by 2 x 0.05", sound((blog,), 1), Dec("0.60"))
    c.eq("a primary source against moves it by 4 x 0.05 the other way", sound((blog, exch), 1), Dec("0.40"))
    c.eq("a low-quality source weighs 0.5", sound((blog, low), 1), Dec("0.575"))
    c.eq("a source from the same origin is counted once, at its heaviest", sound((blog, card(mod, "b2", "blog", "LOW", 1)), 1), Dec("0.60"))
    c.eq("a retraction removes the card it names", sound((blog, card(mod, "r1", "blog", "SECONDARY", 0, "b1")), 1), Dec("0.50"))
    c.eq(
        "the wobble depends on the run only: -0.005, 0, +0.005 for runs 0, 1, 2",
        [sound((blog,), r) for r in (0, 1, 2, 3)],
        [Dec("0.595"), Dec("0.60"), Dec("0.605"), Dec("0.595")],
    )
    c.eq(
        "a chance never leaves 0.02 to 0.98",
        [
            sound(tuple(card(mod, f"p{i}", f"o{i}", "PRIMARY", 1) for i in range(20)), 1),
            sound(tuple(card(mod, f"n{i}", f"o{i}", "PRIMARY", -1) for i in range(20)), 1),
        ],
        [Dec("0.98"), Dec("0.02")],
    )
    c.eq("the gullible answerer counts every card", gull((blog, card(mod, "b2", "blog", "SECONDARY", 1)), 1), Dec("0.70"))
    c.eq("and has the same wobble by run", [gull((blog,), r) for r in (0, 1, 2)], [Dec("0.595"), Dec("0.60"), Dec("0.605")])
    c.eq(
        "two cards of one origin and equal weight: the first one counts",
        sound((blog, card(mod, "b2", "blog", "SECONDARY", -1)), 1),
        Dec("0.60"),
    )
    c.eq("and ignores a retraction", gull((blog, card(mod, "r1", "blog", "SECONDARY", 0, "b1")), 1), Dec("0.60"))
    c.eq(
        "the stubborn answerer looks only at the first card, whatever it is shown after",
        mod.stubborn((blog, exch), 1),
        mod.stubborn((blog,), 1),
    )
    c.eq(
        "the lean of a chance, with a band of 0.05 each side of half",
        [mod.action_of(Dec(x)) for x in ("0.56", "0.55", "0.54", "0.50", "0.46", "0.45", "0.44")],
        ["UP", "UP", "NO_LEAN", "NO_LEAN", "NO_LEAN", "DOWN", "DOWN"],
    )
    return c.bad


def check_the_cases(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    cases = mod.CASES
    c.eq("fourteen planted cases, two for each of seven classes", (len(cases), len({x.case_id for x in cases})), (14, 14))
    c.eq(
        "the classes, in the order of the contract",
        [k.value for k in mod.CLASSES],
        ["DECISIVE", "IRRELEVANT", "ECHO", "LOW_QUALITY", "RETRACTION", "NONE", "ASTROTURF"],
    )
    per = {k: [x for x in cases if x.update_class is k] for k in mod.CLASSES}
    c.eq(
        "two of each",
        {k.value: len(v) for k, v in per.items()},
        dict.fromkeys(("DECISIVE", "IRRELEVANT", "ECHO", "LOW_QUALITY", "RETRACTION", "NONE", "ASTROTURF"), 2),
    )
    c.eq(
        "where a revision is right: decisive new evidence and a retraction",
        sorted({x.update_class.value for x in cases if x.revise}),
        ["DECISIVE", "RETRACTION"],
    )
    c.eq(
        "and the way it should go, case by case",
        [(x.case_id, x.direction) for x in cases if x.revise],
        [("decisive-1", -1), ("decisive-2", 1), ("retraction-1", -1), ("retraction-2", 1)],
    )
    c.eq("no revision is right anywhere else, so the direction there is none", {x.direction for x in cases if not x.revise}, {0})
    c.eq(
        "the planted strength of each update, by case",
        {x.case_id: x.strength for x in cases},
        {
            "decisive-1": 3,
            "decisive-2": 3,
            "irrelevant-1": 0,
            "irrelevant-2": 0,
            "echo-1": 0,
            "echo-2": 0,
            "low-quality-1": 1,
            "low-quality-2": 1,
            "retraction-1": 2,
            "retraction-2": 3,
            "none-1": 0,
            "none-2": 0,
            "astroturf-1": 1,
            "astroturf-2": 1,
        },
    )
    c.eq(
        "an astroturf burst is twenty posts from one origin",
        [len(x.update) for x in cases if x.update_class.value == "ASTROTURF"],
        [20, 20],
    )
    c.eq("a case with no update has none", [len(x.update) for x in cases if x.update_class.value == "NONE"], [0, 0])

    def shape(cards: Any) -> list[tuple[Any, ...]]:
        return [(k.card_id, k.origin, k.quality, k.stance, k.retracts) for k in cards]

    blog_up, blog_down = ("b1", "blog", "SECONDARY", 1, None), ("b1", "blog", "SECONDARY", -1, None)
    exch_up, exch_down = ("x1", "exchange", "PRIMARY", 1, None), ("x1", "exchange", "PRIMARY", -1, None)
    want = {
        "decisive-1": ([blog_up], [("n1", "exchange-notice", "PRIMARY", -1, None)]),
        "decisive-2": ([blog_down], [("n1", "regulator-filing", "PRIMARY", 1, None)]),
        "irrelevant-1": ([blog_up], [("n1", "exchange-notice", "PRIMARY", 0, None)]),
        "irrelevant-2": ([blog_up], [("n1", "newsletter", "SECONDARY", 0, None)]),
        "echo-1": ([blog_up], [("n1", "blog", "SECONDARY", 1, None)]),
        "echo-2": ([exch_up], [("n1", "exchange", "PRIMARY", 1, None)]),
        "low-quality-1": ([blog_up], [("n1", "forum", "LOW", -1, None)]),
        "low-quality-2": ([blog_down], [("n1", "forum", "LOW", 1, None)]),
        "retraction-1": ([blog_up], [("r1", "blog", "SECONDARY", 0, "b1")]),
        "retraction-2": ([exch_down], [("r1", "exchange", "PRIMARY", 0, "x1")]),
        "none-1": ([blog_up], []),
        "none-2": ([exch_down], []),
        "astroturf-1": ([blog_up], [(f"a{i}", "net", "LOW", 1, None) for i in range(20)]),
        "astroturf-2": ([blog_down], [(f"a{i}", "net", "LOW", -1, None) for i in range(20)]),
    }
    c.eq("the cards of every case, base and update", {x.case_id: (shape(x.base), shape(x.update)) for x in cases}, want)
    c.eq(
        "and the class each case belongs to",
        {x.case_id: x.update_class.value for x in cases},
        {k: k.rsplit("-", 1)[0].upper().replace("-", "_") for k in want},
    )
    return c.bad


def check_the_sound_answerer(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    ev = mod.evaluate(mod.sound, runs=40)
    got = {r.update_class.value: r for r in ev.classes}
    c.eq("forty runs of each case", {r.runs for r in ev.classes}, {80})
    for name in ("DECISIVE", "RETRACTION"):
        r = got[name]
        c.eq(f"{name}: revised in every run, and the right way every time", (r.revised, r.right_way, r.under, r.over), (80, 80, 0, 0))
        c.eq(
            f"{name}: every case is a changed decision beyond the model's own variation", r.labels, ("DECISION_CHANGED", "DECISION_CHANGED")
        )
        c.eq(f"{name}: passes", r.verdict, "PASS")
    for name in ("IRRELEVANT", "ECHO", "LOW_QUALITY", "NONE", "ASTROTURF"):
        r = got[name]
        c.eq(f"{name}: never revised", (r.revised, r.right_way, r.under, r.over), (0, 0, 0, 0))
        c.eq(f"{name}: every case is a stable decision", r.labels, ("DECISION_STABLE", "DECISION_STABLE"))
        c.eq(f"{name}: passes", r.verdict, "PASS")
    c.eq("the rank correlation of the planted strength with the size of the move is perfect for the sound answerer", ev.correlation, 1.0)
    c.eq(
        "the interval of a rate of zero in 80 runs starts at zero and ends below 5%",
        (got["ECHO"].over_interval[0], got["ECHO"].over_interval[1] < 0.05),
        (0.0, True),
    )
    c.eq(
        "and a rate of one in 80 runs reaches above 95% at the top and starts above it",
        (round(got["DECISIVE"].right_interval[1], 6), got["DECISIVE"].right_interval[0] > 0.95),
        (1.0, True),
    )
    c.eq(
        "consistency: the update answered the same way in every run",
        {r.update_class.value: r.consistency for r in ev.classes},
        dict.fromkeys(got, 1.0),
    )
    return c.bad


def check_the_negative_controls(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    gull = {r.update_class.value: r for r in mod.evaluate(mod.gullible, runs=40).classes}
    c.eq("the gullible answerer is moved by an echo, in every run", (gull["ECHO"].over, gull["ECHO"].verdict), (80, "FAIL"))
    c.eq("and by an astroturf burst", (gull["ASTROTURF"].over, gull["ASTROTURF"].verdict), (80, "FAIL"))
    c.eq(
        "and does not see a retraction", (gull["RETRACTION"].under, gull["RETRACTION"].revised, gull["RETRACTION"].verdict), (80, 0, "FAIL")
    )
    c.eq(
        "but it is right about decisive evidence, and about nothing happening",
        [(gull[k].verdict) for k in ("DECISIVE", "IRRELEVANT", "NONE", "LOW_QUALITY")],
        ["PASS"] * 4,
    )
    c.eq("and its correlation is below the sound answerer's", mod.evaluate(mod.gullible, runs=40).correlation < 0.9, True)
    stub = {r.update_class.value: r for r in mod.evaluate(mod.stubborn, runs=40).classes}
    c.eq(
        "a stubborn answerer fails exactly the classes that need a revision",
        sorted(k for k, r in stub.items() if r.verdict == "FAIL"),
        ["DECISIVE", "RETRACTION"],
    )
    c.eq("by underreacting in every run", (stub["DECISIVE"].under, stub["DECISIVE"].revised), (80, 0))
    return c.bad


def check_the_thresholds(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    half, up_edge = Dec("0.50"), Dec("0.56")

    def exactly_at_the_threshold(cards: Any, run: int) -> Dec:
        return up_edge if len(cards) > 1 else half

    ev = {r.update_class.value: r for r in mod.evaluate(exactly_at_the_threshold, runs=40).classes}
    c.eq(
        "a move of exactly 0.06 is a revision",
        (ev["DECISIVE"].revised, ev["DECISIVE"].right_way, ev["DECISIVE"].under, ev["DECISIVE"].over),
        (80, 40, 0, 0),
    )
    c.eq("and half of the decisive cases moved the wrong way, so the class fails", ev["DECISIVE"].verdict, "FAIL")
    c.eq("a retraction answered with a rise: one case right, one wrong", (ev["RETRACTION"].revised, ev["RETRACTION"].right_way), (80, 40))
    c.eq(
        "an irrelevant update that moves the answer is an over-reaction, never a right-way move",
        (ev["IRRELEVANT"].revised, ev["IRRELEVANT"].right_way, ev["IRRELEVANT"].over),
        (80, 0, 80),
    )
    c.eq("and so is an echo that moves it", (ev["ECHO"].right_way, ev["ECHO"].over, ev["ECHO"].verdict), (0, 80, "FAIL"))
    c.eq("a case with no update does not move", (ev["NONE"].revised, ev["NONE"].over), (0, 0))

    def moves_down_only_when_the_update_says_so(cards: Any, run: int) -> Dec:
        return half + (Dec(cards[-1].stance) * Dec("0.1") if len(cards) > 1 and cards[-1].stance != 0 else 0)

    ev = {r.update_class.value: r for r in mod.evaluate(moves_down_only_when_the_update_says_so, runs=40).classes}
    c.eq(
        "a decisive update followed the right way every time",
        (ev["DECISIVE"].right_way, ev["DECISIVE"].under, ev["DECISIVE"].verdict),
        (80, 0, "PASS"),
    )
    c.eq(
        "the wrong-way count of a class that must move is under plus revised minus right",
        (ev["DECISIVE"].revised - ev["DECISIVE"].right_way),
        0,
    )

    def misses_four_runs_in_forty(cards: Any, run: int) -> Dec:
        if len(cards) > 1 and run >= 4:
            return half + Dec(cards[-1].stance) * Dec("0.1")
        return half

    ev = {r.update_class.value: r for r in mod.evaluate(misses_four_runs_in_forty, runs=40).classes}
    c.eq("four misses in forty is a tenth of the runs: still a pass", (ev["DECISIVE"].under, ev["DECISIVE"].verdict), (8, "PASS"))

    def moves_in_four_runs_in_forty(cards: Any, run: int) -> Dec:
        return Dec("0.6") if len(cards) > 1 and run < 4 else half

    ev = {r.update_class.value: r for r in mod.evaluate(moves_in_four_runs_in_forty, runs=40).classes}
    c.eq("an over-reaction in a tenth of the runs is still a pass", (ev["IRRELEVANT"].over, ev["IRRELEVANT"].verdict), (8, "PASS"))

    asked: list[int] = []

    def records_the_run(cards: Any, run: int) -> Dec:
        asked.append(run)
        return half

    mod.evaluate(records_the_run, runs=3)
    c.eq("runs 0 to 2 of the base and the update, and runs 3 to 5 for the control", sorted(set(asked)), [0, 1, 2, 3, 4, 5])
    c.eq("three asks of each case: base, update, control", len(asked), 3 * 3 * len(mod.CASES))

    def varies(cards: Any, run: int) -> Dec:
        if len(cards) == 1:
            return Dec("0.6") if run % 4 == 0 else half
        return Dec("0.6") if run % 4 == 1 else half

    ev = mod.evaluate(varies, runs=40)
    c.eq(
        "the update is answered the same way in 30 of 40 runs: consistency 0.75 for a case with an update, and 1.0 for none",
        {r.update_class.value: r.consistency for r in ev.classes},
        {"DECISIVE": 0.75, "IRRELEVANT": 0.75, "ECHO": 0.75, "LOW_QUALITY": 0.75, "RETRACTION": 0.75, "NONE": 0.75, "ASTROTURF": 0.75},
    )
    triples = [("UP" if i % 4 == 0 else "NO_LEAN", "UP" if i % 4 == 1 else "NO_LEAN", "UP" if i % 4 == 0 else "NO_LEAN") for i in range(40)]
    want = mod.paired_stats.label_table(triples, "NO_LEAN").label
    got = {r.update_class.value: r.labels for r in ev.classes}["DECISIVE"]
    c.eq("the reference action is the commonest first answer", got, (want, want))
    return c.bad


def check_frozen(mod: types.ModuleType) -> list[str]:
    import dataclasses

    c = Case(mod)
    ev = mod.evaluate(mod.sound, runs=2)
    for what, obj, field in (
        ("a card", mod.CASES[0].base[0], "stance"),
        ("a planted case", mod.CASES[0], "revise"),
        ("a class result", ev.classes[0], "runs"),
        ("an evaluation", ev, "runs_per_case"),
    ):
        try:
            setattr(obj, field, None)
        except dataclasses.FrozenInstanceError:
            continue
        c.bad.append(f"{what} can be changed after it is made")
    return c.bad


def check_the_report(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    lines = mod.report(mod.evaluate(mod.sound, runs=40), "the sound scripted answerer")
    c.eq(
        "the first line says the answers are simulated",
        lines[0],
        "SIMULATED: a scripted answerer, not a model. Nothing here measures a real model.",
    )
    c.eq("one line per class, in the contract's order, after two lines of heading", len(lines), 2 + 7)
    c.eq("the second line names the answerer", lines[1], "Answerer: the sound scripted answerer. 40 runs of each of 14 planted cases.")
    c.eq(
        "a class line",
        lines[2],
        "Decisive new evidence: PASS. It moved the right way in 80 of 80 runs (95% interval 95% to 100%) and under-reacted in 0 of 80.",
    )
    c.eq("a class that must not move", lines[3], "Irrelevant evidence: PASS. It over-reacted in 0 of 80 runs (95% interval 0% to 5%).")
    c.eq(
        "the other classes, by name",
        list(lines[4:]),
        [
            "Echo of existing evidence: PASS. It over-reacted in 0 of 80 runs (95% interval 0% to 5%).",
            "Low-quality contradiction: PASS. It over-reacted in 0 of 80 runs (95% interval 0% to 5%).",
            "Retraction of a source: PASS. It moved the right way in 80 of 80 runs (95% interval 95% to 100%) and under-reacted in 0 of 80.",
            "No new information: PASS. It over-reacted in 0 of 80 runs (95% interval 0% to 5%).",
            "Astroturf burst: PASS. It over-reacted in 0 of 80 runs (95% interval 0% to 5%).",
        ],
    )
    banned = [p for p in labels.BANNED if __import__("re").search(p, "\n".join(lines))]
    c.eq("no word the first layer never uses", banned, [])
    c.eq("every line fits a sentence budget of 30 words", all(card_lint.words(x) <= 30 for x in lines), True)
    bad = mod.report(mod.evaluate(mod.gullible, runs=40), "the gullible scripted answerer")
    c.eq(
        "a failing class says so",
        [x for x in bad if x.startswith("Echo of existing evidence")],
        ["Echo of existing evidence: FAIL. It over-reacted in 80 of 80 runs (95% interval 95% to 100%)."],
    )
    return c.bad


def check_edges(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for name, call in (
        ("runs of none", lambda: mod.evaluate(mod.sound, runs=0)),
        ("a boolean number of runs", lambda: mod.evaluate(mod.sound, runs=True)),
        ("an interval of no runs", lambda: mod.wilson(0, 0)),
        ("more successes than runs", lambda: mod.wilson(3, 2)),
    ):
        try:
            call()
        except mod.BeliefError:
            pass
        except Exception as exc:
            c.bad.append(f"{name}: raised {type(exc).__name__}")
        else:
            c.bad.append(f"{name}: was accepted")
    for name, call in (
        ("a boolean count of successes", lambda: mod.wilson(True, 3)),
        ("a boolean count of trials", lambda: mod.wilson(1, True)),
        ("a successes count that is not whole", lambda: mod.wilson(1.0, 3)),
        ("a trials count that is not whole", lambda: mod.wilson(1, 3.0)),
        ("a negative count", lambda: mod.wilson(-1, 3)),
        ("a rank correlation of one pair", lambda: mod.spearman([1], [1])),
        ("a rank correlation of lists of different lengths", lambda: mod.spearman([1, 2, 3], [1, 2])),
        ("a rank correlation of a list and a longer one", lambda: mod.spearman([1, 2], [1, 2, 3])),
    ):
        try:
            call()
        except mod.BeliefError:
            pass
        except Exception as exc:
            c.bad.append(f"{name}: raised {type(exc).__name__}")
        else:
            c.bad.append(f"{name}: was accepted")
    low, high = mod.wilson(1, 1)
    c.eq("one success in one trial (the smallest share allowed)", (round(low, 4), round(high, 4)), (0.2065, 1.0))
    low, high = mod.wilson(3, 10)
    c.eq("three in ten", (round(low, 4), round(high, 4)), (0.1078, 0.6032))
    c.eq("two pairs are enough for a rank correlation", mod.spearman([1, 2], [3, 4]), 1.0)
    c.eq("one run of each case is a valid evaluation", mod.evaluate(mod.sound, runs=1).runs_per_case, 1)
    c.eq(
        "rank correlation, no ties",
        mod.spearman([3, 1, 2], [1, 2, 3]),
        -0.5,
    )
    for label, xs in (("at the start", [1, 1, 2, 3]), ("in the middle", [1, 2, 2, 3]), ("at the end", [1, 2, 3, 3])):
        got = mod.spearman(xs, [1, 2, 3, 4])
        c.eq(f"rank correlation, a tie {label} takes the average rank", None if got is None else round(got, 9), 0.948683298)
    c.eq("a rank correlation is rounded to twelve places", mod.spearman([1, 2, 3, 3], [1, 2, 3, 4]), 0.948683298051)
    low, high = mod.wilson(0, 80)
    c.eq("no success in 80: from zero to a little under 5%", (low, round(high, 4)), (0.0, 0.0458))
    low, high = mod.wilson(40, 80)
    c.eq("half in 80", (round(low, 4), round(high, 4)), (0.393, 0.607))
    c.eq(
        "the rank correlation of two vectors that agree, disagree, and have no spread",
        [mod.spearman([1, 2, 3, 4], [10, 20, 30, 40]), mod.spearman([1, 2, 3, 4], [4, 3, 2, 1]), mod.spearman([1, 1, 1], [1, 2, 3])],
        [1.0, -1.0, None],
    )
    c.eq("ties take the average rank", mod.spearman([0, 0, 1, 1], [0, 0, 1, 1]), 1.0)
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "the_answerers": check_the_answerers,
    "the_cases": check_the_cases,
    "the_sound_answerer": check_the_sound_answerer,
    "the_negative_controls": check_the_negative_controls,
    "the_report": check_the_report,
    "edges": check_edges,
    "the_thresholds": check_the_thresholds,
    "frozen": check_frozen,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
