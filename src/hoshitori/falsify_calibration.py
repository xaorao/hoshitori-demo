"""Calibration of the falsification tests on planted scenarios (experiment E7 rebuilt on the standard library).

A test may reduce exposure only if its measured false-alternative rate is low (``falsify.MAX_FALSE_ALTERNATIVE``). This module measures
it: it
plants four scenarios in synthetic one-minute returns with fat tails and changing volatility, runs the real tests on them and counts the
labels. The generator is seeded, so a report is reproducible; its digest is what a ``falsify.Calibration`` names as its source.

* A: a true effect, the token jumps in the ten minutes after the announcement and the market is flat.
* B: the same total move, but under way for the 40 minutes before the announcement.
* C: a market-wide move at the announcement that the token follows with beta 1.2.
* D: nothing.

The false-alternative rate of a test is the share of scenario A in which it favoured the alternative, a true effect wrongly taken for
something
else. The other scenarios are reported to show that the tests do what they are for.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Final

from hoshitori import falsify

SCENARIOS: Final = ("A", "B", "C", "D")
CALIBRATED_TESTS: Final = ("T_ONSET", "T_MARKET")
COUNT_LABELS: Final = falsify.LABELS
JUMP: Final = 4.0 * 0.0008 * math.sqrt(60)  # about four hourly standard deviations in total
BASE_VOL: Final = 0.0008
MARKET_LOAD: Final = 1.2
BLOCK: Final = 360  # minutes of one volatility regime


class CalibrationError(ValueError):
    """An argument that is not valid."""


def min_history(onset: falsify.OnsetParams | None = None, market: falsify.MarketParams | None = None) -> int:
    """The least history before the announcement that both tests can read: the longer of the two windows with the gap before it."""
    o, m = onset or falsify.OnsetParams(), market or falsify.MarketParams()
    return max(m.sigma_gap + max(m.beta_minutes, m.lookback), o.sigma_gap + o.lookback)


def student_t4(rng: random.Random) -> float:
    """A Student t draw with four degrees of freedom scaled to unit variance."""
    z = rng.gauss(0.0, 1.0)
    chi_square = rng.gammavariate(2.0, 2.0)  # four degrees of freedom
    return z / math.sqrt(chi_square / 4.0) / math.sqrt(2.0)


def _whole(value: object, name: str, floor: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < floor:
        raise CalibrationError(f"{name} is a whole number, {floor} or more")


def generate(scenario: str, rng: random.Random, ta: int, h: int) -> tuple[list[float], list[float]]:
    """One synthetic token series and its market series, ``ta + h + 10`` minutes long, with the scenario's effect planted at ``ta``.
    The same
    random numbers are drawn whatever the scenario, so two scenarios from one seed differ only by the planted effect."""
    if scenario not in SCENARIOS:
        raise CalibrationError(f"scenario is one of {SCENARIOS}")
    floor = min_history()
    if isinstance(ta, bool) or not isinstance(ta, int) or ta < floor:
        raise CalibrationError(f"ta is at least {floor} minutes")
    _whole(h, "h", 1)
    n = ta + h + 10
    vols = [BASE_VOL * math.exp(rng.gauss(0.0, 0.3)) for _ in range(n // BLOCK + 2)]
    vol = [vols[i // BLOCK] for i in range(n)]
    market = [vol[i] * 0.8 * student_t4(rng) for i in range(n)]
    token = [MARKET_LOAD * market[i] + vol[i] * 0.7 * student_t4(rng) for i in range(n)]
    if scenario == "A":
        for i in range(ta, ta + 10):
            token[i] += JUMP / 10
    elif scenario == "B":
        for i in range(ta - 40, ta):
            token[i] += JUMP / 40
    elif scenario == "C":
        for i in range(ta, ta + 10):
            market[i] += JUMP / 10 / MARKET_LOAD
            token[i] += JUMP / 10
    return token, market


@dataclass(frozen=True)
class Report:
    trials: int
    seed: int
    ta: int
    h: int
    counts: dict[str, dict[str, dict[str, int]]]
    sha256: str

    def rates(self) -> dict[str, dict[str, dict[str, float]]]:
        return {
            s: {t: {label: n / self.trials for label, n in row.items()} for t, row in tests.items()} for s, tests in self.counts.items()
        }

    def false_alternative(self) -> dict[str, float]:
        """The share of planted true effects (scenario A) that each test took for something else."""
        return {t: self.counts["A"][t]["FAVOURS_ALTERNATIVE"] / self.trials for t in CALIBRATED_TESTS}

    def calibration(self) -> falsify.Calibration:
        return falsify.Calibration(self.false_alternative(), self.trials, self.sha256)

    def text(self) -> str:
        head = f"falsification calibration: seed {self.seed}, {self.trials} trials a scenario, ta {self.ta}, horizon {self.h}"
        lines = [f"{head}, digest {self.sha256}"]
        for scenario in SCENARIOS:
            for test in CALIBRATED_TESTS:
                row = self.counts[scenario][test]
                shares = ", ".join(f"{label} {row[label] / self.trials:.3f}" for label in COUNT_LABELS)
                lines.append(f"  {scenario} {test}: {shares}")
        fa = self.false_alternative()
        lines.append("  false-alternative rate on true effects: " + ", ".join(f"{t} {fa[t]:.3f}" for t in CALIBRATED_TESTS))
        return "\n".join(lines)


def run(trials: int, seed: int, ta: int, h: int) -> Report:
    """Run the real tests on ``trials`` planted series of each scenario."""
    _whole(trials, "trials", 1)
    _whole(seed, "seed", 0)
    rng = random.Random(seed)  # noqa: S311  # a simulation, not a secret
    counts: dict[str, dict[str, dict[str, int]]] = {s: {t: dict.fromkeys(COUNT_LABELS, 0) for t in CALIBRATED_TESTS} for s in SCENARIOS}
    for scenario in SCENARIOS:
        for _ in range(trials):
            token, market = generate(scenario, rng, ta, h)
            counts[scenario]["T_ONSET"][falsify.t_onset(token, ta, h).result] += 1
            counts[scenario]["T_MARKET"][falsify.t_market(token, market, ta, h).result] += 1
    body = {"version": falsify.VERSION, "seed": seed, "trials": trials, "ta": ta, "h": h, "counts": counts}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return Report(trials, seed, ta, h, counts, digest)
