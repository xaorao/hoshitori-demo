"""The guarantee itself, measured: watched at every step, the confidence sequence misses the true running mean in at most ``alpha`` of runs,
while an ordinary fixed-time interval, watched the same way, misses in many more.

Each difference is +1 or -1 with a chance that sets its mean exactly, and the mean changes twice, so the true running average is known at
every step and nothing about it is stationary (the paper's setting: no assumption on how outcomes arise). Seeded, so the counts are fixed.
"""

from __future__ import annotations

import math
import random

from hoshitori import anytime

RUNS = 400
STEPS = 300
ALPHA = 0.05


def mean_at(t: int) -> float:
    return 0.0 if t < 100 else 0.2 if t < 200 else -0.1


def run(rng: random.Random) -> tuple[bool, bool]:
    """Whether, in one run watched at every step, the sequence and the fixed-time interval ever missed the true running mean."""
    ds = [1.0 if rng.random() < (1 + mean_at(t)) / 2 else -1.0 for t in range(STEPS)]
    steps = anytime.sequence(ds, alpha=ALPHA)
    truth = 0.0
    sequence_missed = fixed_missed = False
    total = squares = 0.0
    for t, (d, step) in enumerate(zip(ds, steps, strict=True), start=1):
        truth += (mean_at(t - 1) - truth) / t
        sequence_missed |= not step.lower <= truth <= step.upper
        total += d
        squares += d * d
        if t >= 10:
            mean = total / t
            sd = math.sqrt(max(squares / t - mean * mean, 0.0))
            half = 1.96 * sd / math.sqrt(t)
            fixed_missed |= not mean - half <= truth <= mean + half
    return sequence_missed, fixed_missed


def test_watched_at_every_step_the_sequence_keeps_its_guarantee_and_a_fixed_interval_does_not() -> None:
    rng = random.Random(20261109)
    results = [run(rng) for _ in range(RUNS)]
    sequence_misses = sum(1 for s, _ in results if s)
    fixed_misses = sum(1 for _, f in results if f)
    assert sequence_misses / RUNS <= ALPHA, f"the sequence ever missed in {sequence_misses} of {RUNS} runs"
    assert fixed_misses / RUNS > 2 * ALPHA, f"the fixed-time interval ever missed in only {fixed_misses} of {RUNS} runs"


def test_a_planted_difference_is_found() -> None:
    """Power, not only validity. Measured with this seed: a true difference of 0.2, with differences of +1 or -1 (the noisiest a Brier
    difference can be), is found within 300 steps in 55 of 100 runs. That is the price of an interval that may be watched at every step,
    and why the page says "not distinguishable yet" rather than rank. The floor of 45 catches an interval made needlessly wider."""
    rng = random.Random(7)
    found = 0
    for _ in range(100):
        ds = [1.0 if rng.random() < 0.6 else -1.0 for _ in range(STEPS)]
        found += any(step.lower > 0 for step in anytime.sequence(ds, alpha=ALPHA))
    assert found >= 45, f"found in only {found} of 100 runs (55 measured)"
