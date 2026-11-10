"""Checks of the calibration of the falsification tests (``hoshitori.falsify_calibration``), runnable against a mutant.

The generator draws the same random numbers for every scenario and only then plants the effect, so scenario A minus scenario D is exactly the
planted jump: that difference is the closed-form check. The pinned counts for a small seeded run are a regression record of this
implementation; the comparison with the rates measured by experiment E7 (``compare_with_e7``) is the independent check, and runs as its own
test because it needs a few hundred trials.
"""

from __future__ import annotations

import math
import types
from typing import Any

TA = 700
H = 60


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def near(self, label: str, got: float, want: float, tol: float = 1e-12) -> None:
        if not math.isclose(got, want, rel_tol=tol, abs_tol=tol):
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.CalibrationError as exc:
            if not str(exc).startswith(says):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("scenarios", mod.SCENARIOS, ("A", "B", "C", "D"))
    c.eq("tests calibrated", mod.CALIBRATED_TESTS, ("T_ONSET", "T_MARKET"))
    c.eq("jump", mod.JUMP, 4.0 * 0.0008 * math.sqrt(60))
    c.eq("minimum history", mod.min_history(), 570)
    long_onset = mod.falsify.OnsetParams(lookback=900)
    c.eq("a longer onset window sets the minimum", mod.min_history(onset=long_onset), 1020)
    c.eq("a longer beta window sets the minimum", mod.min_history(market=mod.falsify.MarketParams(beta_minutes=800)), 920)
    c.eq("the boundary history is accepted", len(mod.generate("D", mod.random.Random(1), 570, 1)[0]), 581)
    return c.bad


def check_generator(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    series = {s: mod.generate(s, mod.random.Random(5), TA, H) for s in mod.SCENARIOS}
    n = TA + H + 10
    c.eq("length", [(len(series[s][0]), len(series[s][1])) for s in mod.SCENARIOS], [(n, n)] * 4)
    base_t, base_m = series["D"]
    jump = mod.JUMP
    diff_a = [a - b for a, b in zip(series["A"][0], base_t, strict=True)]
    c.eq("A changes the token only", series["A"][1], base_m)
    c.eq("A touches ten minutes from the announcement", [i for i, d in enumerate(diff_a) if d != 0.0], list(range(TA, TA + 10)))
    c.near("A adds jump / 10 a minute", diff_a[TA], jump / 10, 1e-9)
    diff_b = [a - b for a, b in zip(series["B"][0], base_t, strict=True)]
    c.eq("B changes the token only", series["B"][1], base_m)
    c.eq("B starts forty minutes earlier", [i for i, d in enumerate(diff_b) if d != 0.0], list(range(TA - 40, TA)))
    c.near("B adds jump / 40 a minute", diff_b[TA - 40], jump / 40, 1e-9)
    diff_ct = [a - b for a, b in zip(series["C"][0], base_t, strict=True)]
    diff_cm = [a - b for a, b in zip(series["C"][1], base_m, strict=True)]
    c.eq(
        "C moves both from the announcement",
        ([i for i, d in enumerate(diff_ct) if d != 0.0], [i for i, d in enumerate(diff_cm) if d != 0.0]),
        (list(range(TA, TA + 10)),) * 2,
    )
    c.near("C adds jump / 10 to the token", diff_ct[TA], jump / 10, 1e-9)
    c.near("C adds jump / 12 to the market", diff_cm[TA], jump / 10 / 1.2, 1e-9)
    # the same seed gives the same series; another seed another
    c.eq("repeatable", mod.generate("D", mod.random.Random(5), TA, H), series["D"])
    c.eq("another seed differs", mod.generate("D", mod.random.Random(6), TA, H) != series["D"], True)
    # the token loads on the market with beta 1.2 plus its own noise: the least-squares beta over a long run is near 1.2
    t, m = mod.generate("D", mod.random.Random(11), 6000, H)
    mx, my = math.fsum(m) / len(m), math.fsum(t) / len(t)
    beta = math.fsum((a - mx) * (b - my) for a, b in zip(m, t, strict=True)) / math.fsum((a - mx) ** 2 for a in m)
    c.eq("beta near 1.2", abs(beta - 1.2) < 0.08, True)
    # the fat-tailed draw: unit variance, median absolute value of a Student t with 4 degrees of freedom over root two
    rng = mod.random.Random(3)
    draws = sorted(abs(mod.student_t4(rng)) for _ in range(20000))
    c.eq("median absolute draw", abs(draws[10000] - 0.7407 / math.sqrt(2)) < 0.02, True)
    c.eq("tails are fat", sum(1 for d in draws if d > 4.0) > 100, True)
    c.refuses("unknown scenario", lambda: mod.generate("E", mod.random.Random(1), TA, H), "scenario is one of")
    c.refuses("history too short", lambda: mod.generate("D", mod.random.Random(1), 500, H), "ta is at least")
    c.refuses("zero horizon", lambda: mod.generate("D", mod.random.Random(1), TA, 0), "h is a whole number")
    return c.bad


def check_run(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    report = mod.run(trials=24, seed=7, ta=TA, h=H)
    again = mod.run(trials=24, seed=7, ta=TA, h=H)
    c.eq("the same seed gives the same report", (report.counts, report.sha256), (again.counts, again.sha256))
    c.eq("another seed differs", mod.run(trials=24, seed=8, ta=TA, h=H).counts != report.counts, True)
    c.eq("trials", report.trials, 24)
    c.eq("scenarios in the report", sorted(report.counts), list(mod.SCENARIOS))
    for scenario in mod.SCENARIOS:
        for test in mod.CALIBRATED_TESTS:
            row = report.counts[scenario][test]
            c.eq(f"{scenario} {test} labels", sorted(row), sorted(mod.COUNT_LABELS))
            c.eq(f"{scenario} {test} counts sum to the trials", sum(row.values()), 24)
    rates = report.rates()
    c.eq("a rate table", sorted(rates), list(mod.SCENARIOS))
    c.near(
        "a rate is a count over the trials",
        rates["A"]["T_ONSET"]["FAVOURS_ORIGINAL"],
        report.counts["A"]["T_ONSET"]["FAVOURS_ORIGINAL"] / 24,
    )
    fa = report.false_alternative()
    c.eq(
        "false-alternative rate is the alternative share under planted true effects",
        fa,
        {t: report.counts["A"][t]["FAVOURS_ALTERNATIVE"] / 24 for t in mod.CALIBRATED_TESTS},
    )
    cal = report.calibration()
    c.eq("calibration rates", dict(cal.rates), fa)
    c.eq("calibration trials", cal.trials, 24)
    c.eq("calibration digest is the report's", cal.sha256, report.sha256)
    c.eq("digest is hexadecimal", len(report.sha256), 64)
    # the digest names the counts, the seed and the geometry
    c.eq("a different seed changes the digest", mod.run(trials=24, seed=8, ta=TA, h=H).sha256 != report.sha256, True)
    c.eq("a different geometry changes the digest", mod.run(trials=24, seed=7, ta=TA + 1, h=H).sha256 != report.sha256, True)
    c.eq("the report text names the seed and the trials", ("seed 7" in report.text(), "24 trials" in report.text()), (True, True))
    c.eq("pinned counts of a small seeded run", report.counts, PINNED)
    c.eq("pinned digest of a small seeded run", report.sha256, PINNED_DIGEST)
    c.eq("one trial and seed zero are allowed", mod.run(trials=1, seed=0, ta=TA, h=H).trials, 1)
    c.eq("the report is frozen", mod.Report.__dataclass_params__.frozen, True)
    hand = mod.Report(
        2,
        3,
        TA,
        H,
        {
            s: {t: {"FAVOURS_ORIGINAL": 1, "FAVOURS_ALTERNATIVE": 1, "INCONCLUSIVE": 0, "NOT_TESTABLE": 0} for t in mod.CALIBRATED_TESTS}
            for s in mod.SCENARIOS
        },
        "d" * 64,
    )
    lines = hand.text().split("\n")
    c.eq("the report text has a head, eight rows and the summary", len(lines), 10)
    c.eq("head", lines[0], "falsification calibration: seed 3, 2 trials a scenario, ta 700, horizon 60, digest " + "d" * 64)
    c.eq(
        "a row",
        lines[1],
        "  A T_ONSET: FAVOURS_ORIGINAL 0.500, FAVOURS_ALTERNATIVE 0.500, INCONCLUSIVE 0.000, NOT_TESTABLE 0.000",
    )
    c.eq("summary", lines[9], "  false-alternative rate on true effects: T_ONSET 0.500, T_MARKET 0.500")
    c.refuses("no trials", lambda: mod.run(trials=0, seed=1, ta=TA, h=H), "trials is a whole number")
    c.refuses("a boolean seed", lambda: mod.run(trials=3, seed=True, ta=TA, h=H), "seed is a whole number")  # type: ignore[arg-type]
    return c.bad


def compare_with_e7(mod: types.ModuleType, trials: int = 400) -> list[str]:
    """The independent check: the rates this implementation gives must agree with those measured by the experiment, within the sampling
    error of both. The experiment used numpy's generator and six days of history; this one uses the standard library and a short one, so
    the match is statistical, never exact."""
    c = Case(mod)
    report = mod.run(trials=trials, seed=48, ta=1000, h=H)
    r = report.rates()
    tol = 4.0 * math.sqrt(0.25 / trials) + 0.03
    e7 = {
        ("A", "T_ONSET"): (0.40, 0.05),
        ("B", "T_ONSET"): (0.00, 0.45),
        ("C", "T_ONSET"): (0.42, 0.04),
        ("D", "T_ONSET"): (0.01, 0.02),
        ("A", "T_MARKET"): (0.99, 0.00),
        ("B", "T_MARKET"): (0.07, 0.03),
        ("C", "T_MARKET"): (0.06, 0.70),
        ("D", "T_MARKET"): (0.05, 0.04),
    }
    for (scenario, test), (original, alternative) in e7.items():
        row = r[scenario][test]
        if abs(row["FAVOURS_ORIGINAL"] - original) > tol:
            c.bad.append(f"{scenario} {test} original: {row['FAVOURS_ORIGINAL']:.3f} against E7 {original:.2f}")
        if abs(row["FAVOURS_ALTERNATIVE"] - alternative) > tol:
            c.bad.append(f"{scenario} {test} alternative: {row['FAVOURS_ALTERNATIVE']:.3f} against E7 {alternative:.2f}")
    cal = report.calibration()
    c.eq("both tests are allowed to cut", (cal.allows("T_ONSET"), cal.allows("T_MARKET")), (True, True))
    return c.bad


PINNED_DIGEST = "1cbcba90de48e7970c99617c57ab283a6ab8eb320b459d3ec631d97c1f8e832c"
PINNED: dict[str, dict[str, dict[str, int]]] = {
    "A": {
        "T_MARKET": {"FAVOURS_ALTERNATIVE": 0, "FAVOURS_ORIGINAL": 24, "INCONCLUSIVE": 0, "NOT_TESTABLE": 0},
        "T_ONSET": {"FAVOURS_ALTERNATIVE": 1, "FAVOURS_ORIGINAL": 9, "INCONCLUSIVE": 14, "NOT_TESTABLE": 0},
    },
    "B": {
        "T_MARKET": {"FAVOURS_ALTERNATIVE": 0, "FAVOURS_ORIGINAL": 0, "INCONCLUSIVE": 24, "NOT_TESTABLE": 0},
        "T_ONSET": {"FAVOURS_ALTERNATIVE": 14, "FAVOURS_ORIGINAL": 0, "INCONCLUSIVE": 10, "NOT_TESTABLE": 0},
    },
    "C": {
        "T_MARKET": {"FAVOURS_ALTERNATIVE": 18, "FAVOURS_ORIGINAL": 0, "INCONCLUSIVE": 6, "NOT_TESTABLE": 0},
        "T_ONSET": {"FAVOURS_ALTERNATIVE": 1, "FAVOURS_ORIGINAL": 11, "INCONCLUSIVE": 12, "NOT_TESTABLE": 0},
    },
    "D": {
        "T_MARKET": {"FAVOURS_ALTERNATIVE": 0, "FAVOURS_ORIGINAL": 1, "INCONCLUSIVE": 23, "NOT_TESTABLE": 0},
        "T_ONSET": {"FAVOURS_ALTERNATIVE": 0, "FAVOURS_ORIGINAL": 0, "INCONCLUSIVE": 24, "NOT_TESTABLE": 0},
    },
}


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_generator, check_run):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
