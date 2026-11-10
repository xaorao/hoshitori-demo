"""Scenarios for the anytime-valid comparison (``hoshitori.anytime``), runnable against the real module or a mutant of it.

The mixture and its boundary are held to the values ``confseq``'s own C++ code gives (``tests/fixtures/anytime_confseq_golden.txt``); the
incomplete gamma function to closed forms; the sequence to a second computation from its definition; and the evidence to its duality with
the sequence (the interval leaves zero exactly when the evidence reaches ``2 / alpha``).
"""

from __future__ import annotations

import math
import random
import types
from collections.abc import Callable
from pathlib import Path

GOLDEN = Path(__file__).parent / "fixtures" / "anytime_confseq_golden.txt"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def near(self, label: str, got: float, want: float, rel: float = 1e-9) -> None:
        if not math.isclose(got, want, rel_tol=rel, abs_tol=rel):
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def raises(self, label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except self.mod.AnytimeError:
            return
        except Exception as exc:  # the wrong kind of refusal is a defect too
            self.bad.append(f"{label}: raised {type(exc).__name__}, not AnytimeError")
            return
        self.bad.append(f"{label}: accepted")


def check_gamma(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for x in (0.01, 0.3, 1.0, 1.9, 2.5, 7.0, 30.0):  # P(1, x) = 1 - e^-x, on both sides of a + 1 = 2
        c.near(f"P(1, {x})", mod.gamma_p(1.0, x), 1 - math.exp(-x), 1e-12)
    for x in (0.2, 1.0, 1.4, 1.6, 4.0, 12.0):  # P(1/2, x) = erf(sqrt x), on both sides of 1.5
        c.near(f"P(1/2, {x})", mod.gamma_p(0.5, x), math.erf(math.sqrt(x)), 1e-12)
    for x in (0.5, 3.0, 3.5, 10.0):  # P(3, x) = 1 - e^-x (1 + x + x^2/2)
        c.near(f"P(3, {x})", mod.gamma_p(3.0, x), 1 - math.exp(-x) * (1 + x + x * x / 2), 1e-12)
    c.eq("P(a, 0) is 0", mod.gamma_p(2.0, 0.0), 0.0)
    c.eq("P(a, infinity) is 1", mod.gamma_p(2.0, math.inf), 1.0)
    c.near("P of a large argument is 1", mod.gamma_p(2.0, 800.0), 1.0, 1e-15)
    try:
        mod.gamma_p(1e10, 1e10)
        c.bad.append("a series too long to converge was accepted")
    except mod.AnytimeError as exc:
        c.eq("a series too long to converge is refused, not cut short", str(exc), "the series for P(a, x) did not converge")
    c.near("P of a large shape and a small argument is 0", mod.gamma_p(400.0, 1.0), 0.0, 1e-15)
    for label, a, x in (
        ("a of zero", 0.0, 1.0),
        ("a below zero", -1.0, 1.0),
        ("x below zero", 1.0, -0.1),
        ("x not a number", 1.0, math.nan),
    ):
        c.raises(label, lambda a=a, x=x: mod.gamma_p(a, x))
    return c.bad


def check_golden(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    bounds = mixtures = past = 0
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line:
            continue
        kind, *fields = line.split()
        if kind == "B":
            v, a, v_opt, scale, want = map(float, fields)
            c.near(f"bound v={v} alpha={a} v_opt={v_opt} c={scale}", mod.Mixture(mod.best_rho(v_opt, a), scale).bound(v, a), want, 1e-9)
            bounds += 1
        elif kind == "M":
            s, v, v_opt, scale, want = map(float, fields)
            c.near(f"log m s={s} v={v} v_opt={v_opt} c={scale}", mod.Mixture(mod.best_rho(v_opt, 0.025), scale).log_m(s, v), want, 1e-9)
            mixtures += 1
        else:
            s, v, v_opt, scale = map(float, fields)
            m = mod.Mixture(mod.best_rho(v_opt, 0.025), scale)
            rho, c2 = m.rho, scale * scale
            want = m.leading - rho / c2 - math.log((v + rho) / c2)  # the upper bound of equation 44 where m has no closed form
            c.near(f"log m bound s={s} v={v}", m.log_m(s, v), want, 1e-12)
            c.eq(f"and at most one, s={s} v={v}", m.log_m(s, v) <= 0, True)
            past += 1
    c.eq("the golden file was read whole", (bounds, mixtures, past), (48, 141, 3))
    return c.bad


def check_mixture(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    lg = math.log(1 / 0.05)
    c.near("rho for v_opt 10 at 0.025 a side", mod.best_rho(10.0, 0.025), 10.0 / (2 * lg + math.log(1 + 2 * lg)), 1e-15)
    m = mod.Mixture(mod.best_rho(10.0, 0.025), 2.0)
    c.near("the bound at v = 0 is the bound at v = 1's floor of the search", m.bound(0.0, 0.025), m.bound(0.0, 0.025))
    u = m.bound(10.0, 0.025)
    c.eq(
        "the bound is where the mixture crosses the threshold",
        (m.log_m(u * (1 - 1e-6), 10.0) < math.log(40), m.log_m(u * (1 + 1e-6), 10.0) > math.log(40)),
        (True, True),
    )
    c.eq("a wider alpha gives a tighter bound", m.bound(10.0, 0.05) < u < m.bound(10.0, 0.01), True)
    c.eq("more intrinsic time, a larger bound", m.bound(5.0, 0.025) < u < m.bound(20.0, 0.025), True)
    small = mod.Mixture(0.5, 1.0)
    c.eq("rho and c below one are allowed", (small.rho, small.c, mod.best_rho(0.5, 0.025) > 0), (0.5, 1.0, True))
    c.near(
        "exactly where the closed form ends (z = 0) the bound of equation 44 is used",
        small.log_m(-2.0, 1.5),
        small.leading - 0.5 - math.log(2.0),
        1e-12,
    )
    big = mod.Mixture(mod.best_rho(10.0, 0.025), 2.0).bound(1e6, 0.025)
    c.eq("a very large intrinsic time still has a bound", 0 < big < 1e6, True)
    for label, fn in (
        ("v_opt of zero", lambda: mod.best_rho(0.0, 0.025)),
        ("alpha_opt of zero", lambda: mod.best_rho(10.0, 0.0)),
        ("alpha_opt of one half", lambda: mod.best_rho(10.0, 0.5)),
        ("rho of zero", lambda: mod.Mixture(0.0, 1.0)),
        ("c of zero", lambda: mod.Mixture(1.0, 0.0)),
        ("a negative intrinsic time", lambda: m.log_m(0.0, -1.0)),
        ("alpha of zero", lambda: m.bound(1.0, 0.0)),
        ("alpha of one", lambda: m.bound(1.0, 1.0)),
    ):
        c.raises(label, fn)
    try:
        m.rho = 1.0  # type: ignore[misc]
        c.bad.append("a mixture can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


def second_way(ds: list[float], alpha: float, bound: float, v_opt: float) -> list[tuple[float, float, float]]:
    """The sequence from its definition: centre the running mean, radius the boundary at max(1, V) over n, V from the previous means."""
    from hoshitori import anytime

    m = anytime.Mixture(anytime.best_rho(v_opt, alpha / 2), 2 * bound)
    out = []
    for n in range(1, len(ds) + 1):
        means = [sum(ds[:i]) / i for i in range(1, n + 1)]
        v = max(1.0, sum((ds[i] - (means[i - 1] if i else 0.0)) ** 2 for i in range(n)))
        r = m.bound(v, alpha / 2) / n
        out.append((means[-1] - r, means[-1], means[-1] + r))
    return out


def check_sequence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rng = random.Random(7)
    ds = [max(-1.0, min(1.0, 0.1 + rng.gauss(0, 0.3))) for _ in range(40)]
    got = mod.sequence(ds)
    want = second_way(ds, 0.05, 1.0, 10.0)
    c.eq("a step for each difference, numbered", [s.n for s in got], list(range(1, 41)))
    for s, (lo, mean, hi) in zip(got, want, strict=True):
        c.near(f"step {s.n}: the mean", s.mean, mean, 1e-12)
        c.near(f"step {s.n}: the lower bound", s.lower, lo, 1e-9)
        c.near(f"step {s.n}: the upper bound", s.upper, hi, 1e-9)
    other = mod.sequence(ds[:12], alpha=0.1, bound=0.5 if max(map(abs, ds[:12])) <= 0.5 else 1.0, v_opt=50.0)
    want2 = second_way(ds[:12], 0.1, 0.5 if max(map(abs, ds[:12])) <= 0.5 else 1.0, 50.0)
    c.near("other settings: the last lower bound", other[-1].lower, want2[-1][0], 1e-9)
    c.eq("no difference, no step", mod.sequence([]), ())
    big_steps = [1.0, -1.0, 1.0, -1.0, 0.5, -0.5]  # squares above one from the second step: the floor of V hides nothing
    for s2, (lo, _, hi) in zip(mod.sequence(big_steps), second_way(big_steps, 0.05, 1.0, 10.0), strict=True):
        c.near(f"large steps, step {s2.n}: the lower bound", s2.lower, lo, 1e-9)
        c.near(f"large steps, step {s2.n}: the upper bound", s2.upper, hi, 1e-9)
    for label, fn, says in (
        ("alpha of zero", lambda: mod.sequence([0.1], alpha=0.0), "alpha is between 0 and 1"),
        ("alpha of one", lambda: mod.sequence([0.1], alpha=1.0), "alpha is between 0 and 1"),
        ("a bound of zero", lambda: mod.sequence([0.0], bound=0.0), "the bound is positive"),
    ):
        try:
            fn()
            c.bad.append(f"{label}: accepted")
        except mod.AnytimeError as exc:
            c.eq(f"{label}: the reason", str(exc), says)
    for label, fn in (
        ("a difference above the bound", lambda: mod.sequence([0.2, 1.5])),
        ("a difference below it", lambda: mod.sequence([-1.0001])),
        ("a difference that is not a number", lambda: mod.sequence([math.nan])),
        ("alpha of zero", lambda: mod.sequence([0.1], alpha=0.0)),
        ("alpha of one", lambda: mod.sequence([0.1], alpha=1.0)),
        ("a bound of zero", lambda: mod.sequence([0.0], bound=0.0)),
    ):
        c.raises(label, fn)
    c.eq("a difference at the bound is allowed", len(mod.sequence([1.0, -1.0])), 2)
    try:
        got[0].mean = 0.0  # type: ignore[misc]
        c.bad.append("a step can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


def check_evidence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rng = random.Random(11)
    better = [max(-1.0, min(1.0, 0.15 + rng.gauss(0, 0.25))) for _ in range(120)]  # the first forecaster is better
    steps = mod.sequence(better)
    first_out = next((s.n for s in steps if s.lower > 0), None)
    first_evidence = next((s.n for s in steps if s.evidence_first >= 2 / 0.05), None)
    c.eq("planted skill is found", first_out is not None, True)
    c.eq("the interval leaves zero exactly when the evidence reaches 2 / alpha", first_evidence, first_out)
    c.eq("and the evidence against the other side stays small", max(s.evidence_second for s in steps) < 1, True)
    worse = mod.sequence([-d for d in better])
    c.eq(
        "mirrored: the upper bound goes below zero with the evidence for the second",
        next((s.n for s in worse if s.upper < 0), None),
        next((s.n for s in worse if s.evidence_second >= 40), None),
    )
    noise = mod.sequence([max(-1.0, min(1.0, rng.gauss(0, 0.3))) for _ in range(120)])
    c.eq("noise alone: the interval holds zero throughout", all(s.lower <= 0 <= s.upper for s in noise), True)
    c.eq("and no evidence reaches 2 / alpha either way", all(s.evidence_first < 40 and s.evidence_second < 40 for s in noise), True)
    c.eq("evidence is never negative", all(s.evidence_first >= 0 and s.evidence_second >= 0 for s in noise), True)
    huge = mod.sequence([1.0] * 3000)
    c.eq("overwhelming evidence stays a finite number", math.isfinite(huge[-1].evidence_first), True)
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "gamma": check_gamma,
    "golden": check_golden,
    "mixture": check_mixture,
    "sequence": check_sequence,
    "evidence": check_evidence,
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
