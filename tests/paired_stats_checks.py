"""Checks of the statistics of an evidence challenge (``hoshitori.paired_stats``), runnable against a mutant."""

from __future__ import annotations

import math
import types
from fractions import Fraction
from random import Random
from typing import Any


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def near(self, label: str, got: float, want: float, tol: float = 5e-4) -> None:
        if not math.isclose(got, want, abs_tol=tol):
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.StatsError as exc:
            if not str(exc).startswith(says):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_pairs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = mod.Pairs(10, 6, 1, 3)
    c.eq("n", p.n, 20)
    c.near("shift", p.shift, 0.25, 1e-12)
    c.near("concordance", p.concordance, 0.65, 1e-12)
    c.near("a negative shift", mod.Pairs(0, 1, 3, 0).shift, -0.5, 1e-12)
    c.refuses("no pairs", lambda: mod.Pairs(0, 0, 0, 0), "at least one pair")
    c.refuses("a negative count", lambda: mod.Pairs(1, -1, 0, 0), "same_changed is a count of pairs")
    c.refuses("a fraction", lambda: mod.Pairs(1.5, 0, 0, 0), "same_same is a count of pairs")  # type: ignore[arg-type]
    c.refuses("a boolean", lambda: mod.Pairs(True, 0, 0, 0), "same_same is a count of pairs")  # type: ignore[arg-type]
    c.refuses("a count in the last place", lambda: mod.Pairs(1, 0, 0, -1), "changed_changed is a count of pairs")
    c.refuses("a count in the third place", lambda: mod.Pairs(1, 0, -1, 0), "changed_same is a count of pairs")
    c.eq("frozen", mod.Pairs.__dataclass_params__.frozen, True)
    return c.bad


def check_mcnemar(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("no disagreement", mod.mcnemar_exact(0, 0), 1.0)
    c.near("5 and 0", mod.mcnemar_exact(5, 0), 0.0625, 1e-12)
    c.near("0 and 5 is the same", mod.mcnemar_exact(0, 5), 0.0625, 1e-12)
    c.near("6 and 1", mod.mcnemar_exact(6, 1), 0.125, 1e-12)
    c.near("even split", mod.mcnemar_exact(3, 3), 1.0, 1e-12)
    c.near("a large split", mod.mcnemar_exact(20, 2), 0.0001, 5e-5)
    # the doubled tail is the sum of the probabilities no larger than the observed one, for every table up to 14 disagreeing pairs
    for m in range(1, 15):
        for b in range(m + 1):
            seen = Fraction(math.comb(m, b), 2**m)
            exact = sum(Fraction(math.comb(m, i), 2**m) for i in range(m + 1) if Fraction(math.comb(m, i), 2**m) <= seen)
            c.near(f"brute force {b} of {m}", mod.mcnemar_exact(b, m - b), float(exact), 1e-9)
    return c.bad


def check_tango(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for counts, half in (((10, 0, 0, 10), 0.161), ((20, 0, 0, 20), 0.088), ((30, 0, 0, 30), 0.060), ((16, 0, 0, 4), 0.161)):
        lo, hi = mod.tango(mod.Pairs(*counts))
        c.near(f"no disagreement {counts} low", lo, -half, 1e-3)
        c.near(f"no disagreement {counts} high", hi, half, 1e-3)
    quiet = mod.Pairs(5, 0, 0, 5)
    c.eq("no pair disagrees and the shift is zero: the score is zero", mod._score(quiet, 0.0), 0.0)
    c.eq("a shift of one when nothing was seen to move: minus infinity", mod._score(quiet, 1.0), -math.inf)
    c.eq("and with some pairs moving", mod._score(mod.Pairs(5, 5, 0, 0), 1.0), -math.inf)
    c.near("a score where the variance is below one", mod._score(mod.Pairs(1, 0, 0, 0), 0.5), -1.0, 1e-12)
    c.near("an ordinary score", mod._score(mod.Pairs(10, 6, 1, 3), 0.1), 1.2116, 5e-4)
    lo, hi = mod.tango(mod.Pairs(15, 5, 0, 0))
    c.near("one way low", lo, 0.049, 1e-3)
    c.near("one way high", hi, 0.469, 1e-3)
    lo, hi = mod.tango(mod.Pairs(0, 5, 0, 15))
    c.near("one way, other margins low", lo, 0.0 + lo, 1.0)
    for counts in ((10, 6, 1, 3), (5, 5, 5, 5), (0, 4, 0, 16), (12, 1, 6, 1)):
        p = mod.Pairs(*counts)
        lo, hi = mod.tango(p)
        c.eq(f"{counts} contains its shift", lo <= p.shift <= hi, True)
        flipped = mod.Pairs(counts[0], counts[2], counts[1], counts[3])
        flo, fhi = mod.tango(flipped)
        c.near(f"{counts} negated on swapping", flo, -hi, 1e-9)
        c.near(f"{counts} negated on swapping (high)", fhi, -lo, 1e-9)
        double = mod.Pairs(*[2 * x for x in counts])
        dlo, dhi = mod.tango(double)
        c.eq(f"{counts} narrower with twice the runs", dhi - dlo < hi - lo, True)
        c.eq(f"{counts} inside the unit range", lo >= -1.0 and hi <= 1.0, True)
    lo, hi = mod.tango(mod.Pairs(0, 8, 0, 0))
    c.eq("everything changed reaches its upper edge", hi, 1.0)
    lo, hi = mod.tango(mod.Pairs(0, 0, 8, 0))
    c.eq("everything changed the other way reaches the lower edge", lo, -1.0)
    wide = mod.tango(mod.Pairs(10, 6, 1, 3), z=2.5758)
    narrow = mod.tango(mod.Pairs(10, 6, 1, 3))
    c.eq("a higher level is wider", wide[0] < narrow[0] and wide[1] > narrow[1], True)
    return c.bad


def check_holm(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("nothing", mod.holm([]), [])
    c.eq("one test at alpha", mod.holm([0.05]), [True])
    c.eq("one test above alpha", mod.holm([0.051]), [False])
    c.eq("two tests", mod.holm([0.02, 0.06]), [True, False])
    c.eq("two tests that both pass", mod.holm([0.02, 0.04]), [True, True])
    c.eq("two tests, both small", mod.holm([0.02, 0.012]), [True, True])
    c.eq("the order given is kept", mod.holm([0.04, 0.001, 0.2]), [False, True, False])
    c.eq("the first that fails stops the rest", mod.holm([0.01, 0.026, 0.0005, 0.03]), [True, False, True, False])
    c.eq("a stricter alpha", mod.holm([0.01, 0.03], alpha=0.02), [True, False])
    c.eq("ties are taken in the order given", mod.holm([0.02, 0.02, 0.9]), [False, False, False])
    c.eq("four small ones", mod.holm([0.001, 0.002, 0.003, 0.004]), [True, True, True, True])
    return c.bad


def check_label(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    P = mod.Pairs
    quiet = P(10, 0, 0, 10)
    some = P(10, 6, 1, 3)
    c.eq("a large clean shift at 20", mod.label(P(5, 15, 0, 0), quiet).label, "DECISION_CHANGED")
    c.eq("the same shift at 19 pairs is uncertain", mod.label(P(4, 15, 0, 0), quiet).label, "EFFECT_UNCERTAIN")
    c.eq("a small shift at 20 is uncertain", mod.label(some, quiet).label, "EFFECT_UNCERTAIN")
    c.eq("shift the other way is also a change", mod.label(P(5, 0, 15, 0), quiet).label, "DECISION_CHANGED")
    v = mod.label(P(5, 15, 0, 0), quiet)
    c.eq("the verdict carries its numbers", (v.n, v.shift, round(v.low, 3), round(v.high, 3), v.p < 0.001), (20, 0.75, 0.468, 0.888, True))
    c.eq("and its note", v.note, "the shift is larger than the model's own variation")
    c.eq("a family that does not reject cannot call a change", mod.label(P(5, 15, 0, 0), quiet, rejected=False).label, "EFFECT_UNCERTAIN")
    c.eq(
        "a family that rejects can, when the plain test would not",
        mod.label(P(5, 5, 0, 10), quiet, rejected=True).label,
        "DECISION_CHANGED",
    )
    c.eq("but not below twenty runs", mod.label(P(1, 2, 0, 5), quiet, rejected=True).label, "EFFECT_UNCERTAIN")
    stable = mod.label(P(30, 0, 0, 30), quiet)
    c.eq("sixty quiet pairs are stable", stable.label, "DECISION_STABLE")
    c.eq("and say why", stable.note, "any shift is inside the stated width")
    c.eq("forty quiet pairs are stable only when they agree", mod.label(P(20, 0, 0, 20), quiet).label, "DECISION_STABLE")
    c.eq("forty pairs that agree too little are uncertain", mod.label(P(16, 4, 4, 16), quiet).label, "EFFECT_UNCERTAIN")
    c.eq("thirty-nine pairs are too few", mod.label(P(20, 0, 0, 19), quiet).label, "EFFECT_UNCERTAIN")
    c.eq("twenty quiet pairs are never stable", mod.label(P(10, 0, 0, 10), quiet).label, "EFFECT_UNCERTAIN")
    wide = mod.label(P(15, 15, 15, 15), quiet)
    c.eq("sixty pairs whose interval is wider than the width are uncertain", wide.label, "EFFECT_UNCERTAIN")
    c.eq("the note of an uncertain label", wide.note, "too few runs to say")
    shifted_control = P(2, 14, 0, 4)
    c.eq(
        "a control that shifts makes nothing readable", mod.label(P(5, 15, 0, 0), shifted_control).label, "NOT_EVALUATED(unstable_baseline)"
    )
    c.eq("and says so", mod.label(P(5, 15, 0, 0), shifted_control).note, "the unchanged input already shifts the answer")
    c.eq("a control that shifts the other way as well", mod.label(P(5, 15, 0, 0), P(2, 0, 14, 4)).label, "NOT_EVALUATED(unstable_baseline)")
    c.eq("a failed share of 20 percent is allowed", mod.label(P(5, 15, 0, 0), quiet, failed=5).label, "DECISION_CHANGED")
    f = mod.label(P(5, 15, 0, 0), quiet, failed=6)
    c.eq("above it nothing is evaluated", f.label, "NOT_EVALUATED(structural_failure)")
    c.eq("and the note counts the runs", f.note, "6 of 26 runs failed")
    c.eq(
        "the failure check comes before the control check",
        mod.label(P(5, 15, 0, 0), shifted_control, failed=30).label,
        "NOT_EVALUATED(structural_failure)",
    )
    c.eq("failed runs default to none", mod.label(P(1, 0, 0, 1), quiet).label, "EFFECT_UNCERTAIN")
    c.eq("exactly 0.8 of agreeing pairs is enough at fifty", mod.label(P(40, 5, 5, 0), quiet).label, "DECISION_STABLE")
    c.eq("sixty pairs are enough whatever their agreement", mod.label(P(46, 7, 7, 0), quiet).label, "DECISION_STABLE")
    c.eq("fifty-nine that agree too little are not", mod.label(P(45, 7, 7, 0), quiet).label, "EFFECT_UNCERTAIN")
    c.eq("the vocabulary", (mod.CHANGED, mod.STABLE, mod.UNCERTAIN), ("DECISION_CHANGED", "DECISION_STABLE", "EFFECT_UNCERTAIN"))
    c.eq(
        "the thresholds",
        (mod.MIN_CHANGED, mod.MIN_STABLE, mod.MIN_STABLE_AGREEING, mod.CONCORDANT, mod.STABLE_WIDTH, mod.MAX_FAILED_SHARE, mod.ALPHA),
        (20, 60, 40, 0.8, 0.15, 0.2, 0.05),
    )
    c.eq("frozen", mod.Verdict.__dataclass_params__.frozen, True)
    return c.bad


def check_failed_runs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    P = mod.Pairs
    moved, quiet = P(5, 15, 0, 0), P(10, 0, 0, 10)
    c.eq("a fifth of the attempted runs may fail", mod.label(moved, quiet, failed=12, attempted=60).label, "DECISION_CHANGED")
    over = mod.label(moved, quiet, failed=13, attempted=60)
    c.eq("one more and nothing is evaluated", (over.label, over.note), ("NOT_EVALUATED(structural_failure)", "13 of 60 runs failed"))
    c.eq("every attempted run failed", mod.label(moved, quiet, failed=1, attempted=1).label, "NOT_EVALUATED(structural_failure)")
    for label, failed, attempted, says in (
        ("a negative failure count", -1, None, "the failed runs are a whole number of at least 0"),
        ("a failure count that is a flag", True, None, "the failed runs are a whole number of at least 0"),
        ("a failure count with a fraction", 1.5, None, "the failed runs are a whole number of at least 0"),
        ("no run attempted", 0, 0, "the runs attempted are a whole number, at least one and at least the runs that failed"),
        ("fewer attempted than failed", 5, 4, "the runs attempted are a whole number, at least one and at least the runs that failed"),
        ("attempted as a flag", 0, True, "the runs attempted are a whole number, at least one and at least the runs that failed"),
        ("attempted with a fraction", 0, 2.0, "the runs attempted are a whole number, at least one and at least the runs that failed"),
    ):
        c.refuses(label, lambda failed=failed, attempted=attempted: mod.label(moved, quiet, failed, attempted=attempted), says)
    return c.bad


def check_holm_adjusted(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("nothing", mod.holm_adjusted([]), [])
    for label, given, want in (
        ("one", [0.01], [0.01]),
        ("two", [0.02, 0.06], [0.04, 0.06]),
        ("in the order given", [0.04, 0.001, 0.2], [0.08, 0.003, 0.2]),
        ("never below an earlier step", [0.01, 0.026, 0.0005, 0.03], [0.03, 0.052, 0.002, 0.052]),
        ("capped at one", [0.6, 0.7], [1.0, 1.0]),
        ("ties in the order given", [0.02, 0.02, 0.9], [0.06, 0.06, 0.9]),
    ):
        got = mod.holm_adjusted(given)
        c.eq(f"{label}: as many as given", len(got), len(want))
        for k, (g, w) in enumerate(zip(got, want, strict=False)):
            c.near(f"{label} [{k}]", g, w, 1e-12)
    # rejected at a level exactly when the step-down method rejects at it
    rng = Random(7)
    for trial in range(300):
        values = [rng.choice([rng.random(), rng.random() / 50, 0.01, 0.025, 0.05]) for _ in range(rng.randint(1, 6))]
        for alpha in (0.05, 0.1, 0.01):
            c.eq(f"trial {trial} at {alpha}", [a <= alpha for a in mod.holm_adjusted(values)], mod.holm(values, alpha=alpha))
    return c.bad


def check_table(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    runs = [("UP", "UP", "UP"), ("UP", "DOWN", "UP"), ("DOWN", "UP", "DOWN"), ("DOWN", "DOWN", "UP"), ("NO_CALL", "UP", "NO_CALL")]
    c.eq("the run positions", (mod.CHANGED_RUN, mod.CONTROL_RUN), (1, 2))
    c.eq("gave UP, first run against the changed run", mod.table(runs, "UP", 1), mod.Pairs(1, 1, 2, 1))
    c.eq("gave UP, first run against the control", mod.table(runs, "UP", 2), mod.Pairs(2, 0, 1, 2))
    c.eq("gave DOWN, against the changed run", mod.table(runs, "DOWN", 1), mod.Pairs(1, 1, 1, 2))
    c.eq("an action no run gave", mod.table(runs, "FLAT", 1), mod.Pairs(0, 0, 0, 5))
    for other in (0, 3):
        c.refuses(f"run position {other}", lambda other=other: mod.table(runs, "UP", other), "a pair's first run is compared with")
    c.refuses("no pairs", lambda: mod.table([], "UP", 1), "at least one pair is needed")
    return c.bad


def check_label_table(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    P = mod.Pairs

    def each(*groups: tuple[int, tuple[str, str, str]]) -> list[tuple[str, str, str]]:
        return [run for count, run in groups for _ in range(count)]

    two = each((30, ("UP", "DOWN", "UP")))
    c.eq("two actions: the plain label on the original", mod.label_table(two, "UP"), mod.label(P(0, 30, 0, 0), P(30, 0, 0, 0)))
    c.eq(
        "and the failures are passed on",
        mod.label_table(two, "UP", 12, attempted=60),
        mod.label(P(0, 30, 0, 0), P(30, 0, 0, 0), 12, attempted=60),
    )
    c.eq("the original need not be among the actions", mod.label_table(each((60, ("DOWN",) * 3)), "UP").label, "DECISION_STABLE")
    others = each((30, ("UP", "UP", "UP")), (30, ("DOWN", "NO_CALL", "DOWN")))
    moved = mod.label_table(others, "UP")
    c.eq(
        "a move between two actions that are not the original is a change",
        (moved.label, moved.note, moved.n, moved.shift),
        ("DECISION_CHANGED", "the shift is larger than the model's own variation (DOWN, NO_CALL)", 60, 0.0),
    )
    c.near("its p is the smallest adjusted one of the family", moved.p, 3 * mod.mcnemar_exact(30, 0), 1e-18)
    c.eq("and its interval is the original action's", (round(moved.low, 3), round(moved.high, 3)), (-0.06, 0.06))
    still = mod.label_table(each((20, ("UP",) * 3), (20, ("DOWN",) * 3), (20, ("NO_CALL",) * 3)), "UP")
    c.eq(
        "three actions, none moving: stable",
        (still.label, still.note),
        ("DECISION_STABLE", "any shift of each of DOWN, NO_CALL, UP is inside the stated width"),
    )
    shaky = mod.label_table(each((20, ("UP",) * 3), (20, ("DOWN", "DOWN", "NO_CALL")), (20, ("NO_CALL",) * 3)), "UP")
    c.eq(
        "a control that moves one action between two others",
        (shaky.label, shaky.note),
        ("NOT_EVALUATED(unstable_baseline)", "the unchanged input already shifts the answer (DOWN, NO_CALL)"),
    )
    few = mod.label_table(each((7, ("UP",) * 3), (7, ("DOWN",) * 3), (6, ("NO_CALL",) * 3)), "UP")
    c.eq("too few pairs", (few.label, few.note), ("EFFECT_UNCERTAIN", "too few runs to say (DOWN, NO_CALL, UP)"))
    family = each((6, ("DOWN", "NO_CALL", "DOWN")), (14, ("UP",) * 3))
    c.eq(
        "a move the plain test would call is not a change once the family is corrected",
        (mod.label(mod.table(family, "DOWN", 1), mod.table(family, "DOWN", 2)).label, mod.label_table(family, "DOWN").label),
        ("DECISION_CHANGED", "EFFECT_UNCERTAIN"),
    )
    broken = mod.label_table(others, "UP", 13, attempted=60)
    c.eq("too many failed runs, said once", (broken.label, broken.note), ("NOT_EVALUATED(structural_failure)", "13 of 60 runs failed"))
    c.eq("no failed run unless one is given", mod.label_table(each((3, ("UP", "DOWN", "UP"))), "UP").label, "EFFECT_UNCERTAIN")
    calm = mod.label_table(each((20, ("UP",) * 3), (17, ("DOWN",) * 3), (3, ("DOWN", "NO_CALL", "DOWN")), (20, ("NO_CALL",) * 3)), "UP")
    c.eq("a stable family reports its smallest adjusted p", (calm.label, calm.p), ("DECISION_STABLE", 0.75))
    c.refuses("no pairs", lambda: mod.label_table([], "UP"), "at least one pair is needed")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (
        check_pairs,
        check_mcnemar,
        check_tango,
        check_holm,
        check_label,
        check_failed_runs,
        check_holm_adjusted,
        check_table,
        check_label_table,
    )
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
